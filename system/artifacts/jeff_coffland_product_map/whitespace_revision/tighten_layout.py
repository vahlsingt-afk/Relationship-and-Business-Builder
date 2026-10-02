from copy import deepcopy
from docx import Document
from docx.shared import Inches, Pt
from docx.oxml.ns import qn

SRC = "system/artifacts/jeff_coffland_product_map/whitespace_revision/source.docx"
OUT = "system/artifacts/jeff_coffland_product_map/Genius_Product_Map_McDonalds.docx"
d = Document(SRC)

# Use more of the printable page while retaining comfortable business-document margins.
sec = d.sections[0]
sec.top_margin = Inches(0.46)
sec.bottom_margin = Inches(0.46)
sec.left_margin = Inches(0.64)
sec.right_margin = Inches(0.64)
sec.header_distance = Inches(0.25)
sec.footer_distance = Inches(0.25)

# Tighten vertical rhythm without changing any text or type size.
d.styles["Normal"].paragraph_format.space_after = Pt(2.5)
for name in ("Heading 1", "Heading 2", "Heading 3"):
    st = d.styles[name]
    st.paragraph_format.space_before = Pt(4)
    st.paragraph_format.space_after = Pt(3)
for p in d.paragraphs:
    if p.style.name.startswith("List Bullet"):
        p.paragraph_format.space_after = Pt(1.5)

# Remove the forced page break before Genius Hardware so it follows Kiosk.
for p in d.paragraphs:
    if p.text.strip() == "PRODUCT MAP 4 OF 5":
        prev = p._p.getprevious()
        if prev is not None:
            for br in prev.xpath('.//w:br[@w:type="page"]'):
                br.getparent().remove(br)
        p.paragraph_format.space_before = Pt(8)
        break

# Scale only the tall kiosk photograph; wording and caption remain unchanged.
if len(d.inline_shapes) > 1:
    kiosk = d.inline_shapes[1]
    ratio = kiosk.height / kiosk.width
    kiosk.width = Inches(0.92)
    kiosk.height = int(kiosk.width * ratio)
for idx in range(2, len(d.inline_shapes)):
    pic = d.inline_shapes[idx]
    ratio = pic.height / pic.width
    pic.width = Inches(1.20)
    pic.height = int(pic.width * ratio)

# Reduce cell padding slightly across information tables.
for table in d.tables:
    for row in table.rows:
        for cell in row.cells:
            tc_pr = cell._tc.get_or_add_tcPr()
            tc_mar = tc_pr.first_child_found_in("w:tcMar")
            if tc_mar is not None:
                for side in ("top", "bottom"):
                    el = tc_mar.find(qn(f"w:{side}"))
                    if el is not None:
                        el.set(qn("w:w"), "50")

# Keep each hardware-photo cell compact so the entire visual row stays with the
# hardware description instead of rolling onto a mostly blank page.
if len(d.tables) >= 9:
    hardware_visuals = d.tables[8]
    for cell in hardware_visuals.rows[0].cells:
        for p in cell.paragraphs:
            p.paragraph_format.space_before = Pt(0)
            p.paragraph_format.space_after = Pt(0)

d.save(OUT)
print(OUT)
