"""Collects the four splits of 4.7 (Train, Valid, Calib, Test) by driving the Twin, then fits the scorer and issues certificates, because the
collection of Calib depends on the fitted scorer (D11). It resumes from the counts already in data/labels.db, so it is safe to interrupt.

    python scripts/make_dataset.py [--profile LEAN|STANDARD|FULL] [--scale 1.0] [--speed 20] [--concurrency 8]"""

import sys

from lexi import cli

if __name__ == "__main__":
    sys.exit(cli.main(["certify", *sys.argv[1:]]))
