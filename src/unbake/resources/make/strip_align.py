# An object whose data starts off an 8-byte boundary in the ROM (NOALIGN in text_sizes.mk, the start's address mod 8)
# has the alignment its compiler asks for and the start does not meet dropped from the data sections of its assembly:
# the assembler pads from the section start, so it would pad where the ROM, aligned by address, did not. Off a word
# boundary the ROM packs the data at the byte: all alignment goes and words and halves are spelled as unaligned data.
# `START:data,sdata` limits that to the named sections: the others start on an 8-byte boundary and keep their alignment.
import re, sys
path, out, section = sys.argv[1], [], "text"
head, _, names = sys.argv[2].partition(":")
start, only = int(head), {"rodata" if n == "rdata" else n for n in names.split(",") if n}
for line in open(path):
    found = re.match(r"\s*\.section\s+\.?(\w+)", line) or re.match(r"\s*\.(text|data|rdata|sdata|bss|rodata)\b", line)
    if found:
        section = found.group(1)
    elif section in ("data", "rdata", "rodata", "sdata") and (not only or section.replace("rdata", "rodata") in only):
        asked = re.match(r"\s*\.(p2)?align\b\s*(\d*)", line)
        if asked and (start % 4 or (asked.group(2) and start % (1 << int(asked.group(2))))):
            continue
        if start % 4:
            line = re.sub(r"^(\s*)\.word\b", r"\1.4byte", re.sub(r"^(\s*)\.(half|short)\b", r"\1.2byte", line))
    out.append(line)
open(path, "w").write("".join(out))
