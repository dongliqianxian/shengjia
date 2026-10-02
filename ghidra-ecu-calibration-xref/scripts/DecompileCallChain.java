// Decompile seed functions plus a bounded caller/callee neighborhood.
// @category ECU.Analysis

import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.Instruction;
import ghidra.program.model.listing.InstructionIterator;
import ghidra.program.model.symbol.Reference;

import java.io.File;
import java.io.PrintWriter;
import java.util.ArrayDeque;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.Set;

public class DecompileCallChain extends GhidraScript {
    private static final int MAX_FUNCTIONS = 32;
    private static final int MAX_ASSEMBLY = 1200;

    private Function functionFor(Address address) {
        Function function = currentProgram.getFunctionManager().getFunctionAt(address);
        if (function == null) {
            function = currentProgram.getFunctionManager().getFunctionContaining(address);
        }
        return function;
    }

    private String containingFunction(Address address) {
        Function function = currentProgram.getFunctionManager().getFunctionContaining(address);
        return function == null ? "<no-function>" :
            function.getName() + "@" + function.getEntryPoint();
    }

    private void enqueueNeighbors(Function function, int nextDepth,
            Map<Address, Integer> selected, ArrayDeque<Function> queue) throws Exception {
        for (Function callee : function.getCalledFunctions(monitor)) {
            if (selected.size() >= MAX_FUNCTIONS) return;
            if (!selected.containsKey(callee.getEntryPoint())) {
                selected.put(callee.getEntryPoint(), nextDepth);
                queue.add(callee);
            }
        }
        for (Reference reference : currentProgram.getReferenceManager()
                .getReferencesTo(function.getEntryPoint())) {
            if (!reference.getReferenceType().isCall()) continue;
            Function caller = functionFor(reference.getFromAddress());
            if (caller == null || selected.size() >= MAX_FUNCTIONS) continue;
            if (!selected.containsKey(caller.getEntryPoint())) {
                selected.put(caller.getEntryPoint(), nextDepth);
                queue.add(caller);
            }
        }
    }

    @Override
    protected void run() throws Exception {
        String[] args = getScriptArgs();
        if (args.length < 3) {
            throw new IllegalArgumentException(
                "Usage: DecompileCallChain.java <output.txt> <depth 0..2> <addr> [...]");
        }
        int maxDepth = Integer.parseInt(args[1]);
        if (maxDepth < 0 || maxDepth > 2) {
            throw new IllegalArgumentException("Depth must be 0, 1, or 2");
        }

        Map<Address, Integer> selected = new LinkedHashMap<>();
        ArrayDeque<Function> queue = new ArrayDeque<>();
        for (int i = 2; i < args.length; i++) {
            Function seed = functionFor(toAddr(args[i]));
            if (seed != null && !selected.containsKey(seed.getEntryPoint())) {
                selected.put(seed.getEntryPoint(), 0);
                queue.add(seed);
            }
        }
        while (!queue.isEmpty() && selected.size() < MAX_FUNCTIONS) {
            Function current = queue.remove();
            int depth = selected.get(current.getEntryPoint());
            if (depth < maxDepth) enqueueNeighbors(current, depth + 1, selected, queue);
        }

        DecompInterface decompiler = new DecompInterface();
        decompiler.toggleCCode(true);
        decompiler.toggleSyntaxTree(true);
        decompiler.setSimplificationStyle("decompile");
        if (!decompiler.openProgram(currentProgram)) {
            throw new IllegalStateException("Decompiler failed to open program");
        }

        try (PrintWriter writer = new PrintWriter(new File(args[0]), "UTF-8")) {
            writer.println("PROGRAM=" + currentProgram.getName());
            writer.println("LANGUAGE=" + currentProgram.getLanguageID());
            writer.println("FUNCTION_COUNT=" + selected.size());
            for (Map.Entry<Address, Integer> item : selected.entrySet()) {
                Function function = functionFor(item.getKey());
                if (function == null) continue;
                writer.println("============================================================");
                writer.println("FUNCTION=" + function.getName() + " ENTRY=" +
                    function.getEntryPoint() + " DEPTH=" + item.getValue() +
                    " BODY=" + function.getBody());

                writer.println("CALLERS");
                for (Reference reference : currentProgram.getReferenceManager()
                        .getReferencesTo(function.getEntryPoint())) {
                    if (reference.getReferenceType().isCall()) {
                        writer.println("  from=" + reference.getFromAddress() +
                            " function=" + containingFunction(reference.getFromAddress()));
                    }
                }

                writer.println("CALLEES");
                Set<Function> callees = function.getCalledFunctions(monitor);
                for (Function callee : callees) {
                    writer.println("  " + callee.getName() + "@" + callee.getEntryPoint());
                }

                writer.println("ASSEMBLY");
                InstructionIterator instructions = currentProgram.getListing()
                    .getInstructions(function.getBody(), true);
                int count = 0;
                while (instructions.hasNext() && count < MAX_ASSEMBLY) {
                    Instruction instruction = instructions.next();
                    StringBuilder refs = new StringBuilder();
                    for (Reference reference : instruction.getReferencesFrom()) {
                        if (refs.length() > 0) refs.append(", ");
                        refs.append(reference.getReferenceType()).append("->")
                            .append(reference.getToAddress());
                    }
                    writer.println("  " + instruction.getAddress() + "  " + instruction +
                        (refs.length() == 0 ? "" : "  ; " + refs));
                    count++;
                }
                if (instructions.hasNext()) writer.println("  <assembly truncated>");

                writer.println("DECOMPILE");
                DecompileResults results = decompiler.decompileFunction(function, 90, monitor);
                writer.println("DECOMPILE_COMPLETED=" + results.decompileCompleted());
                if (results.decompileCompleted() && results.getDecompiledFunction() != null) {
                    writer.println(results.getDecompiledFunction().getC());
                }
                else {
                    writer.println("DECOMPILE_ERROR=" + results.getErrorMessage());
                }
            }
        }
        decompiler.dispose();
        println("DecompileCallChain functions=" + selected.size());
    }
}
