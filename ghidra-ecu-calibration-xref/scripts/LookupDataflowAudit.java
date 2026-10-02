// Emit bounded instruction and P-code windows around calibration-address references.
// @category ECU.Analysis

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.pcode.PcodeOp;
import ghidra.program.model.symbol.Reference;

import java.io.File;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;

public class LookupDataflowAudit extends GhidraScript {
    private static class Target {
        String label;
        Address start;
        Address end;
        AddressSet range;
        Set<String> patterns = new LinkedHashSet<>();
    }

    private Target parseTarget(String label, String start, String end) {
        if (label.isEmpty()) {
            throw new IllegalArgumentException("Target label must not be empty");
        }
        Target target = new Target();
        target.label = label;
        target.start = toAddr(start);
        target.end = toAddr(end);
        target.range = new AddressSet(target.start, target.end);
        addPatterns(target, target.start);
        addPatterns(target, target.end);
        return target;
    }

    private void addPatterns(Target target, Address address) {
        String value = address.toString().toLowerCase(Locale.ROOT);
        int colon = value.lastIndexOf(':');
        if (colon >= 0) value = value.substring(colon + 1);
        int i = 0;
        while (i < value.length() - 1 && value.charAt(i) == '0') i++;
        value = value.substring(i);
        target.patterns.add(value);
        target.patterns.add("0x" + value);
    }

    private boolean matches(Target target, Instruction instruction) {
        if (target.range.contains(instruction.getAddress())) return false;
        for (Reference reference : instruction.getReferencesFrom()) {
            Address destination = reference.getToAddress();
            if (destination != null && target.range.contains(destination) &&
                    !reference.getReferenceType().isFlow()) return true;
        }
        if (instruction.getFlowType().isCall() || instruction.getFlowType().isJump()) return false;
        String rendered = instruction.toString().toLowerCase(Locale.ROOT);
        for (String pattern : target.patterns) {
            if (pattern.length() >= 4 && rendered.contains(pattern)) return true;
        }
        return false;
    }

    private String functionName(Address address) {
        Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
        return function == null ? "<no-function>" :
            function.getName() + "@" + function.getEntryPoint();
    }

    private String references(Instruction instruction) {
        StringBuilder builder = new StringBuilder();
        for (Reference reference : instruction.getReferencesFrom()) {
            if (builder.length() > 0) builder.append(", ");
            builder.append(reference.getReferenceType()).append("->")
                .append(reference.getToAddress());
        }
        return builder.toString();
    }

    private String pcode(Instruction instruction) {
        StringBuilder builder = new StringBuilder();
        for (PcodeOp op : instruction.getPcode()) {
            if (builder.length() > 0) builder.append(" | ");
            builder.append(op.toString());
        }
        return builder.toString();
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 6 || (args.length - 3) % 3 != 0) {
            throw new IllegalArgumentException(
                "Usage: LookupDataflowAudit.java <output.txt> <before> <after> <label> <start> <end> [...]");
        }
        int before = Integer.parseInt(args[1]);
        int after = Integer.parseInt(args[2]);
        if (before < 0 || before > 50 || after < 0 || after > 100) {
            throw new IllegalArgumentException("Context windows out of range");
        }

        List<Target> targets = new ArrayList<>();
        for (int i = 3; i < args.length; i += 3) {
            targets.add(parseTarget(args[i], args[i + 1], args[i + 2]));
        }
        Map<String, LinkedHashSet<Address>> hits = new LinkedHashMap<>();
        for (Target target : targets) hits.put(target.label, new LinkedHashSet<>());

        InstructionIterator iterator = currentProgram.getListing().getInstructions(true);
        while (iterator.hasNext() && !monitor.isCancelled()) {
            Instruction instruction = iterator.next();
            for (Target target : targets) {
                if (matches(target, instruction)) hits.get(target.label).add(instruction.getAddress());
            }
        }

        try (PrintWriter writer = new PrintWriter(new File(args[0]), "UTF-8")) {
            writer.println("PROGRAM=" + currentProgram.getName());
            writer.println("LANGUAGE=" + currentProgram.getLanguageID());
            for (Target target : targets) {
                writer.println("============================================================");
                writer.println("TARGET=" + target.label + " RANGE=" + target.range);
                writer.println("HIT_COUNT=" + hits.get(target.label).size());
                for (Address hitAddress : hits.get(target.label)) {
                    Instruction hit = currentProgram.getListing().getInstructionAt(hitAddress);
                    writer.println("------------------------------------------------------------");
                    writer.println("HIT=" + hitAddress + " FUNCTION=" + functionName(hitAddress));
                    Instruction cursor = hit;
                    for (int i = 0; i < before; i++) {
                        Instruction previous = currentProgram.getListing()
                            .getInstructionBefore(cursor.getAddress());
                        if (previous == null) break;
                        cursor = previous;
                    }
                    int count = before + after + 1;
                    for (int i = 0; i < count && cursor != null; i++) {
                        String marker = cursor.getAddress().equals(hitAddress) ? ">>" : "  ";
                        writer.println(marker + " " + cursor.getAddress() + "  " + cursor);
                        String refs = references(cursor);
                        if (!refs.isEmpty()) writer.println("     REFS: " + refs);
                        String ops = pcode(cursor);
                        if (!ops.isEmpty()) writer.println("     PCODE: " + ops);
                        cursor = currentProgram.getListing()
                            .getInstructionAfter(cursor.getAddress());
                    }
                }
            }
        }
        println("LookupDataflowAudit wrote " + args[0]);
    }
}
