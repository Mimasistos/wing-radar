import sys

if "--radar" in sys.argv:        # eski, yalnızca radar yer paylaşımı
    from .app import main
else:
    from .hub import main

main()
