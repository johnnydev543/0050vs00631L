"""Build the static calculator and report distribution, including download links."""
from __future__ import annotations
import re
import shutil
from pathlib import Path
from build_report import main as build_report
from build_calculator import main as build_calculator

ROOT=Path(__file__).resolve().parent


def main() -> None:
    build_report();build_calculator()
    distribution=ROOT/'dist'
    report=(ROOT/'report.html').read_text().replace('href="dist/index.html"','href="index.html"')
    (distribution/'report.html').write_text(report)
    for link in set(re.findall(r'href="([^"]+)"',report)):
        if link.startswith(('http://','https://','#')) or link=='index.html':continue
        source=(ROOT/link).resolve()
        source.relative_to(ROOT)
        if not source.is_file():raise FileNotFoundError(link)
        target=distribution/link;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,target)
    (ROOT/'.nojekyll').write_text('')
    print(distribution)


if __name__=='__main__':main()
