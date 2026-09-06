"""Render the board to a PNG so a human can LOOK at it."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.core import db  # noqa: E402
from app.core.board_repo import (add_task, create_opportunity, record_bounce,  # noqa: E402
                                 record_outbound)
from app.core.cadence import Channel  # noqa: E402
from app.core.tracker import JobCategory, Stage  # noqa: E402
from app.ui.board import BoardWindow  # noqa: E402
from app.ui.board_adapter import board_rows  # noqa: E402

TUE = date(2026, 9, 8)
# Microsoft Store: 1366x768 or larger, 16:9.
STORE_SIZE = (1366, 768)

app = QApplication(sys.argv)

conn = db.connect(":memory:")
db.migrate(conn)

a = create_opportunity(conn, "Round Hill Capital", stage=Stage.IDENTIFIED)
b = create_opportunity(conn, "Landmark Venues", stage=Stage.IDENTIFIED)
c = create_opportunity(conn, "Rocco Forte Hotels", stage=Stage.CONTACTED)
d = create_opportunity(conn, "Mandarin Oriental", stage=Stage.IN_DIALOGUE)
e = create_opportunity(conn, "Highgate", stage=Stage.PHONE_INTERVIEW)
f = create_opportunity(conn, "The Peninsula", stage=Stage.IN_PERSON_INTERVIEW)
g = create_opportunity(conn, "Bob W", stage=Stage.ON_HOLD)
h = create_opportunity(conn, "Grosvenor", stage=Stage.LOST)
i = create_opportunity(conn, "Marriott Feasibility", stage=Stage.WON)
poc = create_opportunity(conn, "Amir Mossanen (Truist)",
                         stage=Stage.CONTACTED, category=JobCategory.MUTUAL_POC)

record_outbound(conn, str(c), Channel.EMAIL, TUE)
record_outbound(conn, str(d), Channel.EMAIL, date(2026, 9, 1))
add_task(conn, str(d), "Second dual touch", date(2026, 9, 15))
add_task(conn, str(e), "Prepare for the call", date(2026, 9, 10))

# One parity defect and one bounce, so the audit banner has something real.
conn.execute("UPDATE opportunities SET status_mirror='open' WHERE id=?", (f,))
cid = conn.execute("INSERT INTO contacts(opportunity_id, name, email, created_at)"
                   " VALUES(?, 'Paul', 'paul@travelfusion.com', 'x')",
                   (b,)).lastrowid
record_bounce(conn, str(b), cid, TUE)
conn.execute("UPDATE opportunities SET stage=1, status_mirror='waiting' WHERE id=?", (b,))
conn.commit()

win = BoardWindow()
rows, findings = board_rows(conn, today=TUE)
win.load(rows, findings)
win.resize(*STORE_SIZE)
win.show()

# Select the bounced opportunity, so the screenshot shows the audit doing its
# job rather than whatever row Qt happened to land on.
# clearSelection first: setCurrentItem moves the CURRENT item but does not
# clear a selection Qt already made, so two rows end up highlighted.
win.tree.clearSelection()
for i in range(win.tree.topLevelItemCount()):
    group = win.tree.topLevelItem(i)
    for j in range(group.childCount()):
        if group.child(j).text(0) == "Landmark Venues":
            win.tree.setCurrentItem(group.child(j))
            group.child(j).setSelected(True)
for _ in range(8):
    app.processEvents()
out = Path(__file__).resolve().parents[1] / "docs" / "board-window.png"
win.grab().save(str(out))
print("wrote", out)
