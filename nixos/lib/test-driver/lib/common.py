from typing import Tuple, Any, Callable, Dict, Iterator, Optional, List
import sys

def eprint(*args: object, **kwargs: Any) -> None:
    print(*args, file=sys.stderr, **kwargs)
