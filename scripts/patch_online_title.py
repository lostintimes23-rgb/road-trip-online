#!/usr/bin/env python3
"""Patch the translated Road Trip title action into an Online Adventure launch."""
from pathlib import Path
import sys


def main() -> None:
    generated = Path(sys.argv[1])
    source = generated / "unit_0002.cpp"
    text = source.read_text(encoding="utf-8")
    include = '#include "ps2/system.hpp"\n'
    if include not in text:
        text = text.replace('#include "units.hpp"\n', '#include "units.hpp"\n' + include, 1)
    label = "L_0020A058:\n"
    # The first selection requests browser sign-in and returns to the title. Once the
    # browser activates the mode, selecting it again enters the original driveable world.
    patch = (
        "L_0020A058:\n"
        "    if (ps2::online_adventure_active()) { goto L_00209F90; }\n"
        "    ps2::request_online_adventure();\n"
        "    goto L_0020A130;\n"
    )
    old_patches = [
        "L_0020A058:\n    ps2::request_online_adventure();\n    goto L_00209F90;\n",
        "L_0020A058:\n    ps2::request_online_adventure();\n    goto L_0020A130;\n",
    ]
    if patch not in text:
        for old_patch in old_patches:
            if old_patch in text:
                text = text.replace(old_patch, patch, 1)
                break
        else:
            if text.count(label) != 1:
                raise SystemExit("expected exactly one translated 2 Player action at 0x20A058")
            text = text.replace(label, patch, 1)
    source.write_text(text, encoding="utf-8", newline="\n")

    source = generated / "unit_000B.cpp"
    text = source.read_text(encoding="utf-8")
    if include not in text:
        text = text.replace('#include "units.hpp"\n', '#include "units.hpp"\n' + include, 1)
    label = "L_0022E780:\n"
    patch = (
        "L_0022E780:\n"
        "    if (ps2::online_adventure_active()) {\n"
        "        st8(0x0177FD9Fu, static_cast<std::uint8_t>('C'));\n"
        "        st8(0x0177FDA0u, static_cast<std::uint8_t>('o'));\n"
        "        st8(0x0177FDA1u, static_cast<std::uint8_t>('i'));\n"
        "        st8(0x0177FDA2u, static_cast<std::uint8_t>('n'));\n"
        "        st8(0x0177FDA3u, static_cast<std::uint8_t>('s'));\n"
        "        st8(0x0177FDA4u, 0u);\n"
        "        goto L_0022E710;\n"
        "    }\n"
    )
    if patch not in text:
        if text.count(label) != 1:
            raise SystemExit("expected exactly one currency-name entry at 0x22E780")
        text = text.replace(label, patch, 1)
    source.write_text(text, encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
