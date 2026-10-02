from docx import Document

SRC = "system/artifacts/jeff_coffland_product_map/Genius_Product_Map_McDonalds.docx"
OUT = "system/artifacts/jeff_coffland_product_map/Genius_Product_Map_McDonalds.docx"
d = Document(SRC)
body = d._element.body

# Page-two content blocks in source order:
# DMB title/table/image/caption, then Drive Thru title/table.
dmb = [body[i] for i in range(20, 24)]
drive = [body[i] for i in range(24, 26)]
anchor = body[20]
for el in drive:
    body.remove(el)
for el in drive:
    anchor.addprevious(el)

# Update only the section title to reflect the new sequence.
for p in d.paragraphs:
    if p.text == "Digital menu boards and drive-thru":
        for r in p.runs:
            if r.text:
                r.text = "Drive-thru and digital menu boards"
                break
        break

d.save(OUT)
print(OUT)
