// Ghidra PRE-script for Renesas V850 / RH850 ECU images (DENSO Toyota et al).
//
// Raw-binary imports have no entry points, so auto-analysis finds almost nothing.
// This seeds disassembly from the reset vector and the interrupt/exception vector
// tables (V850 vector slots are 0x10 apart and each holds a JR disp22).
//
// Usage:
//   analyzeHeadless <proj> <name> -import ECU.bin \
//       -loader BinaryLoader -loader-baseAddr 0x0 \
//       -processor "V850:LE:32:default" \
//       -scriptPath <this dir> -preScript V850Setup.java -postScript BaseFind.java
import ghidra.app.script.GhidraScript;
import ghidra.program.model.address.Address;
import ghidra.program.model.address.AddressSpace;

public class V850Setup extends GhidraScript {

    @Override
    public void run() throws Exception {
        AddressSpace sp = currentProgram.getAddressFactory().getDefaultAddressSpace();
        int seeded = 0;
        for (long v = 0x0L; v < 0x100L; v += 0x10L) if (seedAt(sp, v)) seeded++;
        for (long v = 0x600L; v < 0x800L; v += 0x10L) if (seedAt(sp, v)) seeded++;
        println("SETUP: seeded " + seeded + " entry points");
    }

    private boolean seedAt(AddressSpace sp, long off) {
        try {
            Address a = sp.getAddress(off);
            int hw = getShort(a) & 0xFFFF;
            if (((hw >> 6) & 0x1F) != 0x1E) return false;   // V850 JR opcode pattern
            disassemble(a);
            createFunction(a, null);
            addEntryPoint(a);
            return true;
        } catch (Exception e) {
            return false;
        }
    }
}
