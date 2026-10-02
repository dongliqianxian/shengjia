// Audit direct and textual references to one or more ECU calibration address ranges.
// @category ECU.Analysis

import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSet;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;

import java.io.File;
import java.io.PrintWriter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

public class EcuAddressXrefAudit extends GhidraScript {
    private static class Target {
        String label;
        Address start;
        Address end;
        AddressSet range;
        Set<String> patterns = new LinkedHashSet<>();
        List<Hit> xrefs = new ArrayList<>();
        List<Hit> textHits = new ArrayList<>();
        int ignoredFlowRefs;
    }

    private static class Hit {
        String from;
        String to;
        String type;
        String instruction;
        String function;
        String functionEntry;
    }

    private String json(String value) {
        if (value == null) return "null";
        return "\"" + value.replace("\\", "\\\\")
            .replace("\"", "\\\"")
            .replace("\r", "\\r")
            .replace("\n", "\\n") + "\"";
    }

    private String trimLeadingZeroes(String value) {
        String lower = value.toLowerCase(Locale.ROOT);
        int colon = lower.lastIndexOf(':');
        if (colon >= 0) lower = lower.substring(colon + 1);
        int i = 0;
        while (i < lower.length() - 1 && lower.charAt(i) == '0') i++;
        return lower.substring(i);
    }

    private Target parseTarget(String label, String start, String end) {
        if (label.isEmpty()) {
            throw new IllegalArgumentException("Target label must not be empty");
        }
        Target target = new Target();
        target.label = label;
        target.start = toAddr(start);
        target.end = toAddr(end);
        if (target.start.compareTo(target.end) > 0) {
            throw new IllegalArgumentException("Target start exceeds end: " + label);
        }
        target.range = new AddressSet(target.start, target.end);
        String startText = trimLeadingZeroes(target.start.toString());
        String endText = trimLeadingZeroes(target.end.toString());
        target.patterns.add(startText);
        target.patterns.add("0x" + startText);
        target.patterns.add(endText);
        target.patterns.add("0x" + endText);
        return target;
    }

    private Function functionAt(Address address) {
        return currentProgram.getFunctionManager().getFunctionContaining(address);
    }

    private Hit makeHit(Instruction instruction, Address destination, String type) {
        Hit hit = new Hit();
        hit.from = instruction.getAddress().toString();
        hit.to = destination == null ? null : destination.toString();
        hit.type = type;
        hit.instruction = instruction.toString();
        Function function = functionAt(instruction.getAddress());
        hit.function = function == null ? "<no-function>" : function.getName();
        hit.functionEntry = function == null ? null : function.getEntryPoint().toString();
        return hit;
    }

    private boolean textMatches(Target target, Instruction instruction) {
        if (target.range.contains(instruction.getAddress())) return false;
        if (instruction.getFlowType().isCall() || instruction.getFlowType().isJump()) return false;
        String rendered = instruction.toString().toLowerCase(Locale.ROOT);
        for (String pattern : target.patterns) {
            if (pattern.length() >= 4 && rendered.contains(pattern)) return true;
        }
        return false;
    }

    private void writeHit(PrintWriter writer, Hit hit) {
        writer.print("{\"from\":" + json(hit.from));
        writer.print(",\"to\":" + json(hit.to));
        writer.print(",\"type\":" + json(hit.type));
        writer.print(",\"instruction\":" + json(hit.instruction));
        writer.print(",\"function\":" + json(hit.function));
        writer.print(",\"function_entry\":" + json(hit.functionEntry));
        writer.print("}");
    }

    private void writeHits(PrintWriter writer, List<Hit> hits) {
        writer.print("[");
        for (int i = 0; i < hits.size(); i++) {
            if (i > 0) writer.print(",");
            writeHit(writer, hits.get(i));
        }
        writer.print("]");
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 4 || (args.length - 1) % 3 != 0) {
            throw new IllegalArgumentException(
                "Usage: EcuAddressXrefAudit.java <output.json> <label> <start> <end> [...]");
        }

        List<Target> targets = new ArrayList<>();
        for (int i = 1; i < args.length; i += 3) {
            targets.add(parseTarget(args[i], args[i + 1], args[i + 2]));
        }

        InstructionIterator iterator = currentProgram.getListing().getInstructions(true);
        while (iterator.hasNext() && !monitor.isCancelled()) {
            Instruction instruction = iterator.next();
            for (Target target : targets) {
                boolean direct = false;
                for (Reference reference : instruction.getReferencesFrom()) {
                    Address destination = reference.getToAddress();
                    if (destination != null && target.range.contains(destination)) {
                        if (target.range.contains(instruction.getAddress()) ||
                                reference.getReferenceType().isFlow()) {
                            target.ignoredFlowRefs++;
                            continue;
                        }
                        target.xrefs.add(makeHit(instruction, destination,
                            reference.getReferenceType().toString()));
                        direct = true;
                    }
                }
                if (!direct && textMatches(target, instruction)) {
                    target.textHits.add(makeHit(instruction, null, "INSTRUCTION_TEXT"));
                }
            }
        }

        try (PrintWriter writer = new PrintWriter(new File(args[0]), "UTF-8")) {
            writer.print("{\"program\":" + json(currentProgram.getName()));
            writer.print(",\"language\":" + json(currentProgram.getLanguageID().toString()));
            writer.print(",\"compiler\":" +
                json(currentProgram.getCompilerSpec().getCompilerSpecID().toString()));
            writer.print(",\"targets\":[");
            for (int i = 0; i < targets.size(); i++) {
                if (i > 0) writer.print(",");
                Target target = targets.get(i);
                writer.print("{\"label\":" + json(target.label));
                writer.print(",\"start\":" + json(target.start.toString()));
                writer.print(",\"end\":" + json(target.end.toString()));
                writer.print(",\"xrefs\":");
                writeHits(writer, target.xrefs);
                writer.print(",\"text_hits\":");
                writeHits(writer, target.textHits);
                writer.print(",\"ignored_flow_refs\":" + target.ignoredFlowRefs);
                writer.print("}");
            }
            writer.println("]}");
        }

        int total = 0;
        for (Target target : targets) {
            total += target.xrefs.size() + target.textHits.size();
            println(target.label + " direct=" + target.xrefs.size() +
                " text=" + target.textHits.size() +
                " ignored_flow=" + target.ignoredFlowRefs);
        }
        println("EcuAddressXrefAudit total hits=" + total);
    }
}
