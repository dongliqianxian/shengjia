// Ghidra POST-script: the "global base register" unlock (SKILL.md, that section).
//
// Problem it solves: on V850/RH850 (and TriCore/SH/PowerPC with their own base
// registers) calibration is reached as `base_register + 16-bit displacement`.
// The instruction never contains an address, so xref search and address-literal
// search both return NOTHING. This script recovers the base from the reset path
// and re-resolves every base-relative load to an absolute address.
//
// Optional args (via -postScript BaseFind.java <anchor_hex>):
//   anchor_hex : an address you already believe in (e.g. a gear-ratio table).
//                The script reports which instructions read it, if any.
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;
import ghidra.program.model.listing.*;
import ghidra.program.model.lang.Register;
import ghidra.program.model.scalar.Scalar;
import java.util.*;

public class BaseFind extends GhidraScript {

    static boolean plausible(long v) {
        // flash-ish or internal-RAM-ish; widen if your device differs
        return (v <= 0x00200000L) || (v >= 0x03F00000L && v <= 0x04000000L);
    }

    @Override
    public void run() throws Exception {
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        Listing lst = currentProgram.getListing();
        Register gp = currentProgram.getRegister("gp");
        Register tp = currentProgram.getRegister("tp");
        Register ep = currentProgram.getRegister("ep");

        long anchor = -1;
        String[] args = getScriptArgs();
        if (args.length > 0) {
            try { anchor = Long.decode(args[0]); } catch (Exception e) { }
        }

        // ---- decode the V850 reset vector: JR disp22 at offset 0 ----
        int hw0 = getShort(sp.getAddress(0)) & 0xFFFF;
        int hw1 = getShort(sp.getAddress(2)) & 0xFFFF;
        long disp = ((long) (hw0 & 0x3F) << 16) | (hw1 & 0xFFFE);
        if ((disp & 0x200000L) != 0) disp -= 0x400000L;
        long reset = disp & 0xFFFFFFFFL;
        println("reset vector JR -> 0x" + Long.toHexString(reset));
        println("");

        println("=== A. startup disassembly (base-register writes marked) ===");
        Address a = sp.getAddress(reset);
        if (lst.getInstructionAt(a) == null) disassemble(a);
        Instruction p = lst.getInstructionAt(a);
        for (int i = 0; i < 220 && p != null; i++) {
            boolean w = false;
            for (Object o : p.getResultObjects())
                if (o instanceof Register) {
                    Register r = (Register) o;
                    if (r.equals(gp) || r.equals(tp) || r.equals(ep)) w = true;
                }
            println(String.format("   %s  %-46s%s", p.getAddress(), p.toString(),
                    w ? "   <<<< BASE REG" : ""));
            Instruction nx = lst.getInstructionAfter(p.getAddress());
            if (nx == null) {
                Address na = p.getAddress().add(p.getLength());
                disassemble(na);
                nx = lst.getInstructionAt(na);
            }
            p = nx;
        }
        println("");

        println("=== B. base-register constant loads with a plausible value ===");
        List<long[]> tpCands = new ArrayList<>();
        int n = 0;
        for (InstructionIterator it = lst.getInstructions(true); it.hasNext(); ) {
            Instruction ins = it.next();
            boolean isTp = false, isAny = false;
            for (Object o : ins.getResultObjects())
                if (o instanceof Register) {
                    Register r = (Register) o;
                    if (r.equals(tp)) { isTp = true; isAny = true; }
                    else if (r.equals(gp) || r.equals(ep)) isAny = true;
                }
            if (!isAny) continue;
            String mn = ins.getMnemonicString();
            if (!(mn.equals("mov") || mn.equals("movhi") || mn.equals("movea"))) continue;
            Scalar s = null;
            for (int i = 0; i < ins.getNumOperands(); i++)
                for (Object o : ins.getOpObjects(i))
                    if (o instanceof Scalar) s = (Scalar) o;
            if (s == null || !plausible(s.getUnsignedValue())) continue;
            println(String.format("   %s  %-46s  imm=0x%X", ins.getAddress(), ins.toString(),
                    s.getUnsignedValue()));
            if (isTp && mn.equals("mov"))
                tpCands.add(new long[]{s.getUnsignedValue(), ins.getAddress().getOffset()});
            if (++n > 40) { println("   ...truncated"); break; }
        }
        println("");

        println("=== C. score tp candidates by how many loads resolve in-image ===");
        long best = -1; int bestScore = -1;
        long maxAddr = currentProgram.getMemory().getMaxAddress().getOffset();
        for (long[] c : tpCands) {
            int score = 0, total = 0;
            for (InstructionIterator it = lst.getInstructions(true); it.hasNext(); ) {
                Instruction ins = it.next();
                if (!ins.getMnemonicString().startsWith("ld")) continue;
                boolean usesTp = false; Scalar d = null;
                for (int i = 0; i < ins.getNumOperands(); i++)
                    for (Object o : ins.getOpObjects(i)) {
                        if (o instanceof Register && ((Register) o).equals(tp)) usesTp = true;
                        if (o instanceof Scalar) d = (Scalar) o;
                    }
                if (!usesTp || d == null) continue;
                total++;
                long ea = c[0] + d.getSignedValue();
                if (ea >= 0 && ea <= maxAddr) score++;
            }
            println(String.format("   tp=0x%08X (set @0x%X): %d/%d tp-loads resolve in-image",
                    c[0], c[1], score, total));
            if (score > bestScore) { bestScore = score; best = c[0]; }
        }
        if (best < 0) { println("   NO tp candidate found — widen plausible() or check the core"); return; }
        println(String.format("   -> chosen tp = 0x%08X", best));
        println("");

        // ---- resolve everything ----
        Map<Long, List<Instruction>> byEa = new TreeMap<>();
        for (InstructionIterator it = lst.getInstructions(true); it.hasNext(); ) {
            Instruction ins = it.next();
            String mn = ins.getMnemonicString();
            if (!(mn.startsWith("ld") || mn.startsWith("st"))) continue;
            boolean usesTp = false; Scalar d = null;
            for (int i = 0; i < ins.getNumOperands(); i++)
                for (Object o : ins.getOpObjects(i)) {
                    if (o instanceof Register && ((Register) o).equals(tp)) usesTp = true;
                    if (o instanceof Scalar) d = (Scalar) o;
                }
            if (!usesTp || d == null) continue;
            byEa.computeIfAbsent((best + d.getSignedValue()) & 0xFFFFFFFFL,
                                 k -> new ArrayList<>()).add(ins);
        }
        println("=== D. resolved tp-relative effective addresses: " + byEa.size() + " ===");

        if (anchor >= 0) {
            println("");
            println(String.format("=== E. instructions reading the anchor 0x%06X (+/-16B) ===", anchor));
            for (long e = anchor - 16; e <= anchor + 16; e++) {
                if (!byEa.containsKey(e)) continue;
                StringBuilder sb = new StringBuilder();
                for (Instruction i2 : byEa.get(e)) {
                    Function f = getFunctionContaining(i2.getAddress());
                    sb.append(i2.getAddress());
                    if (f != null) sb.append("(").append(f.getName()).append(")");
                    sb.append(" ");
                }
                println(String.format("   0x%06X  x%d  %s", e, byEa.get(e).size(), sb.toString().trim()));
            }
            println("");
            long lo = anchor - 0x400, hi = anchor + 0x400;
            println(String.format("=== F. every resolved EA in 0x%06X-0x%06X ===", lo, hi));
            for (Map.Entry<Long, List<Instruction>> en : byEa.entrySet()) {
                long ea = en.getKey();
                if (ea < lo || ea > hi) continue;
                StringBuilder sb = new StringBuilder();
                for (Instruction i2 : en.getValue()) sb.append(i2.getAddress()).append(" ");
                println(String.format("   0x%06X  x%d  %s", ea, en.getValue().size(),
                        sb.toString().trim()));
            }
        }
    }
}
