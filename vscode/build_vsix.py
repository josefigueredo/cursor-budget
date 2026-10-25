"""Build the Cursor extension (.vsix) with the standard library only: a .vsix is a zip with a manifest.

    python3 vscode/build_vsix.py [out_dir]     # writes out_dir/cursor-budget-<version>.vsix (default: vscode/dist)

install.sh builds it and installs it with `cursor --install-extension`, so no node or vsce is needed.
"""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
FILES = ("package.json", "extension.js", "format.js", "panel.js")

CONTENT_TYPES = """<?xml version="1.0" encoding="utf-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension=".json" ContentType="application/json"/>
<Default Extension=".js" ContentType="application/javascript"/>
<Default Extension=".vsixmanifest" ContentType="text/xml"/>
</Types>
"""

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011" \
xmlns:d="http://schemas.microsoft.com/developer/vsx-schema-design/2011">
  <Metadata>
    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}"/>
    <DisplayName>{display}</DisplayName>
    <Description xml:space="preserve">{description}</Description>
    <Categories>Other</Categories>
    <GalleryFlags>Public</GalleryFlags>
    <Properties>
      <Property Id="Microsoft.VisualStudio.Code.Engine" Value="{engine}"/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionDependencies" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionPack" Value=""/>
      <Property Id="Microsoft.VisualStudio.Code.ExtensionKind" Value="ui"/>
      <Property Id="Microsoft.VisualStudio.Code.LocalizedLanguages" Value=""/>
    </Properties>
  </Metadata>
  <Installation>
    <InstallationTarget Id="Microsoft.VisualStudio.Code"/>
  </Installation>
  <Dependencies/>
  <Assets>
    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true"/>
  </Assets>
</PackageManifest>
"""


def build(out_dir: Path) -> Path:
    package = json.loads((HERE / "package.json").read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)
    vsix = out_dir / f"{package['name']}-{package['version']}.vsix"
    manifest = MANIFEST.format(name=escape(package["name"]), version=escape(package["version"]),
                               publisher=escape(package["publisher"]), display=escape(package["displayName"]),
                               description=escape(package["description"]), engine=escape(package["engines"]["vscode"]))
    with zipfile.ZipFile(vsix, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", CONTENT_TYPES)
        z.writestr("extension.vsixmanifest", manifest)
        for name in FILES:
            z.write(HERE / name, f"extension/{name}")
    return vsix


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "dist"))
