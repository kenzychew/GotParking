"""Process entry point: ``python -m gotparking_training`` runs the weekly job."""

from __future__ import annotations

import sys

from gotparking_training.train import main

if __name__ == "__main__":
    sys.exit(main())
