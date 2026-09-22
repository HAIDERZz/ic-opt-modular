"""Deck: the templated netlists for every testbench × corner, stored under ``.icopt/decks/<fp>/``."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

Key = tuple[str, str | None]  # (testbench id, corner id or None)


@dataclass
class Deck:
    templates: dict[Key, str] = field(default_factory=dict)   # template text per testbench × corner
    source: dict[str, str] = field(default_factory=dict)      # testbench id -> where its deck came from
    bundles: dict[str, Path] = field(default_factory=dict)    # testbench id -> exported netlist dir (support files)

    def template(self, testbench: str, corner: str | None) -> str:
        return self.templates[(testbench, corner)]

    def bundle(self, testbench: str) -> Path | None:
        return self.bundles.get(testbench)

    def fingerprint(self) -> str:
        h = hashlib.sha256()
        for (tb, corner), text in sorted(self.templates.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
            h.update(f"{tb}/{corner}\n".encode())
            h.update(text.encode())
        return h.hexdigest()[:16]

    def save(self, root: Path) -> Path:
        target = root / self.fingerprint()
        for (tb, corner), text in self.templates.items():
            path = target / tb / (corner or "nominal") / "template.scs"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
        for tb, bundle in self.bundles.items():
            dst = target / tb / "bundle"
            if bundle.resolve() != dst.resolve():
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(bundle, dst, symlinks=False)
        (target / "source.txt").write_text("".join(f"{tb}\t{src}\n" for tb, src in sorted(self.source.items())))
        return target

    @classmethod
    def load(cls, path: Path) -> Deck:
        deck = cls()
        for bundle in sorted(path.glob("*/bundle")):
            deck.bundles[bundle.parent.name] = bundle
        for template in sorted(path.glob("*/*/template.scs")):
            corner = template.parent.name
            deck.templates[(template.parent.parent.name, None if corner == "nominal" else corner)] = template.read_text(
                encoding="utf-8"
            )
        source = path / "source.txt"
        if source.exists():
            for line in source.read_text().splitlines():
                tb, _, src = line.partition("\t")
                deck.source[tb] = src
        return deck
