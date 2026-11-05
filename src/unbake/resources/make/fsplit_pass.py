# A compiler run with its function-sections flag gives each function a section of its own (`.Fn`), but its assembler
# front rewrites the assembly and puts every function back in `.text`, every string, jump table and float pool in one
# `.rdata`. FSPLIT (text_sizes.mk: an object whose functions or read-only data the ROM places apart) puts each function
# in `.text.Fn` again and tags each read-only block with the function it belongs to, as `.rdata.Fn`: a block inside the
# function (jump table, float pool) is that function's. Each source global opens its own section; compiler labels
# remain in their function's literal pool, so the link script lays every item at its ROM address.
# A block the ROM holds at 4 mod 8 (POOLPHASE, `key:phase` arguments from text_sizes.mk) was laid in the original
# single `.rdata` by address, but the object's section starts on an 8: an `.align 3` inside it pads against the section
# start and would miss the pad word the ROM has. The pass spells that pad out against the block's address instead.
import re, sys
phase = {k: int(v) for k, _, v in (a.partition(":") for a in sys.argv[3:]) if int(v) % 8}
lines = open(sys.argv[1]).read().replace("\r", "").split("\n")
code = re.compile(r"^\.section\s+\.([^\s,\"]+),\"ax\"")
reopen = re.compile(r"^\.section\s+\.([^\s,\"]+)\Z")
names = [m.group(1) for m in map(code.match, lines) if m]
funcs = set(names)
label_re = re.compile(r"^((?!RODATA_SYM_)[A-Za-z_][A-Za-z_0-9]*):")  # not the compiler front's own pool labels
def next_block(i):
    for j in range(i + 1, len(lines)):
        if lines[j].startswith(".section") or lines[j].strip() in (".text", ".data"):
            return j
    return len(lines)
def next_section(i):
    for j in range(i + 1, len(lines)):
        text = lines[j].strip()
        if text.startswith((".section", ".data")) or text == ".text":
            return lines[j]
    return ""
data = re.compile(r"\s*\.(word|half|short|byte|float|double|dword|quad|ascii|asciz|asciiz|string|space|skip)\b")
def first_align(block):
    """The `.align` a block's first item is placed by (0 when an item comes before any)."""
    for x in block:
        m = re.match(r"\s*\.(align|p2align|balign)\s+(\d+)", x)
        if m:
            return (int(m.group(2)).bit_length() - 1) if m.group(1) == "balign" else int(m.group(2))
        if data.match(x):
            return 0
    return 0
# the compiler writes a pool where the previous function ends or where its own code is: the function a pool belongs to
# is the first one whose code names a label of it
label_any = re.compile(r"^([.A-Za-z_][\w.]*):")
token = re.compile(r"[.A-Za-z_][\w.]*")
owner, at, in_code = {}, None, False
for line in lines:
    found = code.match(line) or (reopen.match(line) if reopen.match(line) and reopen.match(line).group(1) in funcs else None)
    if found:
        at, in_code = found.group(1), True
    elif line.strip() == ".text":
        in_code = True
    elif re.match(r"\s*\.(section|data)\b", line):
        in_code = False
    elif in_code and at and not label_any.match(line):
        for word in token.findall(line):
            owner.setdefault(word, at)
def split_globals(source):
    """Give source globals their own blocks, carrying their leading alignment with them."""
    result = []
    i = 0
    while i < len(source):
        if not re.match(r"^\s*\.section\s+\.(rdata|rodata)\s*\Z", source[i]):
            result.append(source[i])
            i += 1
            continue
        end = next_block(i)
        start, kind = i + 1, None
        chunks = []
        for j in range(start, end):
            label = label_any.match(source[j])
            if not label:
                continue
            source_label = label_re.match(source[j])
            # Compiler labels stay together as a function pool. A source label always
            # opens a new section, even when the compiler emitted no section directive.
            new_kind = "global" if source_label else "pool"
            if kind is not None and (source_label or kind != new_kind):
                boundary = j
                while boundary > start and re.match(
                    r"^\s*(?:$|#|\.(?:align|p2align|balign|globl|global|type)\b)", source[boundary - 1]
                ):
                    boundary -= 1
                chunks.append((kind, source[start:boundary]))
                start = boundary
            kind = new_kind
        chunks.append((kind, source[start:end]))
        for kind, chunk in chunks:
            result.append(".section .rdata" if kind == "global" else source[i])
            result.extend(chunk)
        i = end
    return result

lines = split_globals(lines)
sized = {"word": 4, "4byte": 4, "long": 4, "int": 4, "float": 4, "half": 2, "short": 2, "2byte": 2, "byte": 1,
         "double": 8, "dword": 8, "quad": 8, "8byte": 8}
plain = {"globl", "global", "type", "size", "set", "weak", "local", "ent", "end", "frame", "mask", "fmask", "file", "ident"}
quoted = re.compile(r'"((?:[^"\\]|\\.)*)"')
escape = re.compile(r"\\(?:[0-7]{1,3}|x[0-9A-Fa-f]+|.)")
def size_of(line):
    """The bytes a line of data takes (0 for none), None when the pass cannot tell."""
    m = re.match(r"\s*\.(\w+)\s*(.*)", line)
    if not m or re.match(r"\s*[.\w$]+:", line):
        return 0
    op, rest = m.groups()
    if op in sized:
        return sized[op] * len([x for x in rest.split("#")[0].split(",") if x.strip()])
    if op in ("ascii", "asciz", "asciiz", "string"):
        return sum(len(escape.sub("x", t)) + (op != "ascii") for t in quoted.findall(rest))
    if op in ("space", "skip"):
        n = rest.split(",")[0].strip()
        return int(n, 0) if re.fullmatch(r"0[xX][0-9A-Fa-f]+|\d+", n) else None
    return 0 if op in plain else None
out, current, opened = [], None, set()
block, used = None, {}  # the phased block being written, and the bytes of each written so far (None: not known)
for i, line in enumerate(lines):
    if re.match(r"\s*\.(section|data|text)\b", line):
        block = None
    elif used.get(block) is not None:
        asked = re.match(r"\s*\.(p2align|align|balign)\s+(\d+)", line)
        if asked:
            width = int(asked.group(2)) if asked.group(1) == "balign" else 1 << int(asked.group(2))
            if width > 4 and not used[block]:
                used[block] = None  # one before the block's first item: the link script moves the block to the 8 (__align8_)
            else:
                pad = -(phase[block] + used[block]) % width
                used[block] += pad
                if width > 4:  # a word alignment holds on any section start; only a wider one can pad against the wrong phase
                    # the assembler moves a label written just before `.align` to the aligned place: so does this
                    names_before = []
                    while out and re.fullmatch(r"\s*[.\w$]+:\s*", out[-1]):
                        names_before.insert(0, out.pop())
                    out += (["\t.space %d" % pad] if pad else []) + names_before
                    continue
        else:
            size = size_of(line)
            used[block] = None if size is None else used[block] + size
    found = code.match(line)
    if found:
        current = found.group(1)
        out.append('.section\t.text.%s,"ax",@progbits' % current)
    elif reopen.match(line) and reopen.match(line).group(1) in funcs:
        current = reopen.match(line).group(1)
        out.append('.section\t.text.%s,"ax",@progbits' % current)
    elif line.strip() == ".text":
        out.append('.section\t.text.%s,"ax",@progbits' % current if current else line)
    elif re.match(r"^\s*\.section\s+\.(rdata|rodata)\s*\Z", line):
        base = "rdata" if "rdata" in line else "rodata"
        label = next((m.group(1) for m in map(label_re.match, lines[i + 1 : next_block(i)]) if m), None)
        # a block is named as the ROM names it: its first source label, else the pool of the function it sits in (strings before the first function: that one's)
        user = next((owner[m.group(1)] for m in map(label_any.match, lines[i + 1 : next_block(i)]) if m and m.group(1) in owner), None)
        key = label or ("literals_" + (user or current or names[0]) if names else None)
        out.append('.section\t.%s.%s,"a",@progbits' % (base, key) if key else line)
        # `SUBALIGN(1)` drops the section's alignment: a block whose first item is aligned to 8 says so to the link
        # script. A `.align 3` further in (a double after a float) pads inside the block, never moves its start, and
        # the compiler writes a pool in several pieces: only the piece that opens the block holds its first item
        if key and key not in opened and first_align(lines[i + 1 : next_block(i)]) >= 3:
            out.append("\t.weak __align8_%s\n\t.set __align8_%s, 0" % (key, key))
        opened.add(key)
        if key in phase:
            block = key
            used.setdefault(key, 0)
    else:
        out.append(line)
open(sys.argv[2], "w").write("\n".join(out))
