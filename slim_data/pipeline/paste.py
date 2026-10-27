"""paste/1 (D-88): a table pasted in, as copied from Excel, for a lab that works as it did in Excel.

The columns and checks are generic_csv/1's; only the separator differs: Excel puts a selection on the
clipboard as tab-separated text, quoting a cell that holds a tab or a line break. The run records
the kind PASTE, which the lab's policy may accept or refuse.
"""

from typing import ClassVar

from slim_data.domain.enums import SourceKind
from slim_data.pipeline.generic_csv import GenericCsv


class Paste(GenericCsv):
    name: ClassVar[str] = "paste"
    version: ClassVar[int] = 1
    source_kind: ClassVar[SourceKind] = SourceKind.PASTE
    delimiter: ClassVar[str] = "\t"
