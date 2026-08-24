// Dump every base-register-relative load/store displacement, with counts.
//
// Use when BaseFind.java cannot locate the `mov <imm32>, tp` (startup not
// reached by auto-analysis, or the image sets the base some other way).
// You then SOLVE for the base instead of reading it:
//
//   if you already trust two addresses A and B (e.g. a value block and a table
//   proven by data-side evidence), the true base must satisfy
//       base + d1 = A   and   base + d2 = B
//   so d1 - d2 == A - B. Find observed displacement pairs with that exact
//   difference and the base falls out. Adjacent fields (A, A+2) give a second,
//   independent constraint.
//
// Output is CSV-ish: reg,disp_hex,disp_dec,count,first_instruction
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.scalar.Scalar;
import java.util.*;

public class DumpBaseDisp extends GhidraScript {

    @Override
    public void run() throws Exception {
        Listing lst = currentProgram.getListing();
        String[] names = {"tp", "gp", "ep"};
        for (String rn : names) {
            Register r = currentProgram.getRegister(rn);
            if (r == null) continue;
            Map<Long, int[]> counts = new TreeMap<>();
            Map<Long, String> firstAt = new HashMap<>();
            for (InstructionIterator it = lst.getInstructions(true); it.hasNext(); ) {
                Instruction ins = it.next();
                String mn = ins.getMnemonicString();
                boolean isLoad = mn.startsWith("ld") || mn.startsWith("sld");
                boolean isStore = mn.startsWith("st") || mn.startsWith("sst");
                if (!isLoad && !isStore) continue;
                boolean uses = false;
                Scalar d = null;
                for (int i = 0; i < ins.getNumOperands(); i++)
                    for (Object o : ins.getOpObjects(i)) {
                        if (o instanceof Register && ((Register) o).equals(r)) uses = true;
                        if (o instanceof Scalar) d = (Scalar) o;
                    }
                if (!uses || d == null) continue;
                long disp = d.getSignedValue();
                int[] c = counts.computeIfAbsent(disp, k -> new int[2]);
                if (isLoad) c[0]++; else c[1]++;
                firstAt.putIfAbsent(disp, ins.getAddress().toString());
            }
            println("### BASE " + rn + " : " + counts.size() + " distinct displacements");
            for (Map.Entry<Long, int[]> e : counts.entrySet()) {
                println(String.format("%s,%s0x%X,%d,%d,%d,%s", rn,
                        e.getKey() < 0 ? "-" : "", Math.abs(e.getKey()), e.getKey(),
                        e.getValue()[0], e.getValue()[1], firstAt.get(e.getKey())));
            }
            println("### END " + rn);
        }
    }
}
