"""Exercise object-list publication with a paused preprocessor seam, without make or timing assumptions."""

import os
import shlex
import subprocess
import sys
from contextlib import suppress

from unbake import config


def test_object_list_stays_complete_while_another_stamp_make_rewrites_it(tmp_path):
    target = tmp_path / "build/us-rev1/objs.mk"
    target.parent.mkdir(parents=True)
    before = b"OBJS_us-rev1 := build/us-rev1/before.o\n"
    target.write_bytes(before)
    ready_r, ready_w = os.pipe()
    release_r, release_w = os.pipe()
    cpp = tmp_path / "cpp.py"
    cpp.write_text(
        f"import os\nos.write({ready_w}, b'R')\nos.read({release_r}, 1)\nprint('SOURCE/reproduced.o(.text);')\n"
    )
    rule = config.template("Makefile.in").split("$(BUILD)/%/objs.mk: $(NAME).ld\n", 1)[1]
    # Expand only the make variables of this recipe; the real shell pipeline and publication remain intact.
    script = "\n".join(rule.splitlines()[:2])
    for name, value in (
        ("$(Q)", ""),
        ("$(BUILD)", "build"),
        ("$(@D)", str(target.parent)),
        ("$(CPP)", f"{shlex.quote(sys.executable)} {shlex.quote(str(cpp))}"),
        ("$(VMACRO_$*)", "VERSION_US_REV1"),
        ("$*", "us-rev1"),
        ("$<", "slice.ld"),
        ("$@", str(target)),
        ("$$$$", "$$"),
    ):
        script = script.replace(name, value)
    child = subprocess.Popen(
        ["sh", "-c", script],
        cwd=tmp_path,
        pass_fds=(ready_w, release_r),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    os.close(ready_w)
    os.close(release_r)
    try:
        assert os.read(ready_r, 1) == b"R"
        assert target.read_bytes() == before  # another make must never parse the writer's partial assignment
    finally:
        with suppress(BrokenPipeError):  # a failed preprocessor has already closed its end
            os.write(release_w, b"G")
        os.close(release_w)
        os.close(ready_r)
        stdout, stderr = child.communicate()
    assert child.returncode == 0, (stdout, stderr)
    assert target.read_bytes() == b"OBJS_us-rev1 := build/us-rev1/SOURCE/reproduced.o\n"
