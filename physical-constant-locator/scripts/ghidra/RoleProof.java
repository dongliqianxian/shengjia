// Prove the SEMANTIC ROLE of a located constant from how the code uses it.
//
// args: <tp_hex> <anchor_hex> [span_bytes]
// Resolves every tp-relative load with the given base, lists the instructions
// that read the anchor window, then dumps + decompiles the owning functions
// with each tp-load annotated with its absolute address.
//
// Read the arithmetic (SKILL.md "从算术用法反推语义角色"):
//   multiplier + squared -> dimensionless ratio
//   divisor    + squared -> a length
//   base + index*2       -> a table, not a scalar
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.scalar.Scalar;
import java.util.*;

public class RoleProof extends GhidraScript {

    @Override
    public void run() throws Exception {
        String[] a = getScriptArgs();
        if (a.length < 2) { println("need <tp_hex> <anchor_hex> [span]"); return; }
        long TP = Long.decode(a[0]);
        long ANCH = Long.decode(a[1]);
        int span = a.length > 2 ? Integer.decode(a[2]) : 6;

        Listing lst = currentProgram.getListing();
        Register tp = currentProgram.getRegister("tp");
        println("tp = 0x" + Long.toHexString(TP) + "   anchor = 0x" + Long.toHexString(ANCH)
                + " span " + span + "\n");

        Map<Long, List<Instruction>> byEa = new TreeMap<>();
        for (InstructionIterator it = lst.getInstructions(true); it.hasNext(); ) {
            Instruction ins = it.next();
            String mn = ins.getMnemonicString();
            if (!(mn.startsWith("ld") || mn.startsWith("st"))) continue;
            boolean uses = false; Scalar d = null;
            for (int i = 0; i < ins.getNumOperands(); i++)
                for (Object o : ins.getOpObjects(i)) {
                    if (o instanceof Register && ((Register) o).equals(tp)) uses = true;
                    if (o instanceof Scalar) d = (Scalar) o;
                }
            if (!uses || d == null) continue;
            byEa.computeIfAbsent((TP + d.getSignedValue()) & 0xFFFFFFFFL,
                                 k -> new ArrayList<>()).add(ins);
        }
        println("resolved tp-relative EAs: " + byEa.size() + "\n");

        println("=== read sites in the anchor window ===");
        LinkedHashSet<Function> funcs = new LinkedHashSet<>();
        for (long e = ANCH; e < ANCH + span; e++) {
            if (!byEa.containsKey(e)) continue;
            StringBuilder sb = new StringBuilder();
            for (Instruction i2 : byEa.get(e)) {
                Function f = getFunctionContaining(i2.getAddress());
                sb.append(i2.getAddress());
                if (f != null) { sb.append("(").append(f.getName()).append(")"); funcs.add(f); }
                sb.append(" ");
            }
            println(String.format("  0x%06X  x%d  %s", e, byEa.get(e).size(), sb.toString().trim()));
        }
        println("");

        println("=== full neighbourhood 0x" + Long.toHexString(ANCH - 0x400)
                + " - 0x" + Long.toHexString(ANCH + 0x400) + " ===");
        for (Map.Entry<Long, List<Instruction>> en : byEa.entrySet()) {
            long ea = en.getKey();
            if (ea < ANCH - 0x400 || ea > ANCH + 0x400) continue;
            StringBuilder sb = new StringBuilder();
            for (Instruction i2 : en.getValue()) sb.append(i2.getAddress()).append(" ");
            println(String.format("  0x%06X  x%d  %s", ea, en.getValue().size(), sb.toString().trim()));
        }
        println("");

        DecompInterface di = new DecompInterface();
        di.openProgram(currentProgram);
        for (Function f : funcs) {
            println("=========================================================");
            println("FUNCTION " + f.getName() + " @" + f.getEntryPoint());
            println("=========================================================");
            Instruction p = lst.getInstructionAt(f.getEntryPoint());
            int c = 0;
            while (p != null && f.getBody().contains(p.getAddress()) && c < 260) {
                String extra = "";
                for (int i = 0; i < p.getNumOperands(); i++) {
                    boolean u = false; Scalar d = null;
                    for (Object o : p.getOpObjects(i)) {
                        if (o instanceof Register && ((Register) o).equals(tp)) u = true;
                        if (o instanceof Scalar) d = (Scalar) o;
                    }
                    if (u && d != null)
                        extra = String.format("      ; = [0x%06X]",
                                (TP + d.getSignedValue()) & 0xFFFFFFFFL);
                }
                println(String.format("   %s  %-34s%s", p.getAddress(), p.toString(), extra));
                p = lst.getInstructionAfter(p.getAddress());
                c++;
            }
            try {
                DecompileResults r = di.decompileFunction(f, 240, monitor);
                if (r.decompileCompleted()) {
                    println("---- decompiled ----");
                    println(r.getDecompiledFunction().getC());
                }
            } catch (Exception ex) { }
            println("");
        }
        di.dispose();
    }
}
