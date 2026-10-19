"""Build (do not launch) the owned WinForms desktop qualification window."""
from pathlib import Path
import os
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    framework = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework64" / "v4.0.30319"
    compiler = framework / "csc.exe"
    if not compiler.is_file():
        raise RuntimeError("The optional fixture requires the Windows .NET Framework C# compiler")
    output = root / ".jarvis" / "qualification" / "ControlEngineFixture.exe"
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(compiler), "/nologo", "/target:winexe", "/out:" + str(output),
                    "/reference:System.Windows.Forms.dll", "/reference:System.Drawing.dll",
                    "/reference:System.Web.Extensions.dll", str(root / "scripts" / "ControlEngineFixture.cs")],
                   check=True, timeout=30)
    print(output)


if __name__ == "__main__":
    main()
