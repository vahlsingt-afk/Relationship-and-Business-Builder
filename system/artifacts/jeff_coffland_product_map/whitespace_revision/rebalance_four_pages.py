from docx import Document

SRC = "system/artifacts/jeff_coffland_product_map/Genius_Product_Map_McDonalds.docx"
OUT = SRC
d = Document(SRC)
body = d._element.body

def find(text):
    for i, el in enumerate(body):
        if text in " ".join(el.xpath('.//w:t/text()')):
            return i
    raise RuntimeError(text)

# Move the Drive Thru product title/table to the bottom of page 1, before the
# page break that currently starts page 2.
dt_i = find("Genius Drive Thru")
dt_block = [body[dt_i], body[dt_i + 1]]
for el in dt_block:
    body.remove(el)
break_i = next(i for i, el in enumerate(body) if el.xpath('.//w:br[@w:type="page"]'))
anchor = body[break_i]
for el in dt_block:
    anchor.addprevious(el)

# Page 2 begins with the DMB. Replace the old combined page heading with a DMB
# heading, then bring Payments and Back Office under it to use the lower space.
for p in d.paragraphs:
    if p.text == "Drive-thru and digital menu boards":
        p.runs[0].text = "Digital menu boards, payments and back office"
        for r in p.runs[1:]: r.text = ""
        break

payments_i = find("Payments and back office")
payment_block = [body[i] for i in range(payments_i, len(body)-1)]
for el in payment_block:
    body.remove(el)

# Insert payment block after DMB image caption and before the next page break.
caption_i = find("Representative Genius system-on-chip")
insert_before = body[caption_i + 1]
for el in payment_block:
    insert_before.addprevious(el)

# Remove the page break that previously preceded Payments, if it moved with the
# block, and remove its redundant section heading label.
for el in list(body):
    text = " ".join(el.xpath('.//w:t/text()'))
    if text == "Payments and back office":
        body.remove(el)
        break
for el in list(body):
    if el.xpath('.//w:br[@w:type="page"]'):
        nxt = el.getnext()
        if nxt is not None and "Genius Payments" in " ".join(nxt.xpath('.//w:t/text()')):
            body.remove(el)

# Update page-one heading and four-page labels to describe the new grouping.
for p in d.paragraphs:
    if p.text == "Commerce and kitchen operations":
        p.runs[0].text = "Commerce, kitchen and drive-thru"
        for r in p.runs[1:]: r.text = ""

# The rebalance produces three pages. Make the counters and final page heading
# match that structure, and remove the redundant second section header on page 3.
for p in d.paragraphs:
    if p.text.startswith("PRODUCT MAP "):
        new = p.text.replace("1 OF 4", "1 OF 3").replace("2 OF 4", "2 OF 3").replace("3 OF 4", "3 OF 3")
        if "4 OF 4" in new:
            p._element.getparent().remove(p._element)
            continue
        p.runs[0].text = new
        for r in p.runs[1:]: r.text = ""
for p in list(d.paragraphs):
    if p.text == "Self-service ordering":
        p.runs[0].text = "Self-service ordering and Genius hardware"
        for r in p.runs[1:]: r.text = ""
    elif p.text == "Genius hardware":
        p._element.getparent().remove(p._element)

d.save(OUT)
print(OUT)
