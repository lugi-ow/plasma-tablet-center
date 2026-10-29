#!/usr/bin/env python3
# Test double for tablet-pen-pos.py: prints $FAKE_PEN ("X Y SW SH"), default screen centre of 2560x1440; --screen prints "SW SH" only.
import os
import sys
pen = os.environ.get("FAKE_PEN", "1280 720 2560 1440")
print(" ".join(pen.split()[2:4]) if "--screen" in sys.argv else pen)
