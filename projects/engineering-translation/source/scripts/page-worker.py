"""Deterministic page geometry/rendering; translation and review stay in the model pipeline."""
import json
import math
import re
import sys
from pathlib import Path

import pymupdf as fitz
from PIL import Image, ImageDraw, ImageFont


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def prepare(source, number, folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with fitz.open(source) as original:
        if original.needs_pass:
            raise ValueError("PDF is password protected")
        one = fitz.open()
        one.insert_pdf(original, from_page=number - 1, to_page=number - 1)
    page = one[0]
    page.remove_rotation()
    width, height = page.rect.width, page.rect.height
    rows = []
    for block_id, block in enumerate(page.get_text("dict")["blocks"]):
        for line in block.get("lines", []):
            spans = line.get("spans", [])
            value = "".join(s["text"] for s in spans).strip()
            if not re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", value):
                continue
            x0, y0, x1, y1 = line["bbox"]
            angle = round(math.degrees(math.atan2(*reversed(line["dir"])) ))
            quad = fitz.recover_line_quad(line)
            rows.append({"source": value, "box": [x0 / width, y0 / height, (x1-x0) / width, (y1-y0) / height],
                         "sourceQuads": [[[p.x/width,p.y/height] for p in [quad.ul,quad.ur,quad.lr,quad.ll]]],
                         "fontSize": max(s["size"] for s in spans), "angle": angle, "origin": "native", "block": block_id})
    # Native PDF blocks are stronger evidence than proximity alone. Merge only
    # prose in a compact block with no cell rule or unrelated text in its union.
    drawings = page.get_drawings()
    grouped = []
    for block_id in dict.fromkeys(r["block"] for r in rows):
        group = [r for r in rows if r["block"] == block_id]
        ordered = sorted(group, key=lambda r: (round(r["box"][1], 3), r["box"][0]))
        x = min(r["box"][0] for r in group); y = min(r["box"][1] for r in group)
        right = max(r["box"][0]+r["box"][2] for r in group)
        bottom = max(r["box"][1]+r["box"][3] for r in group)
        rect = fitz.Rect(x*width, y*height, right*width, bottom*height)
        crossed = any(d["rect"].intersects(rect) and (d["rect"].width > rect.width*.7 or d["rect"].height > rect.height*.7) for d in drawings)
        other_text = any(r["block"] != block_id and overlap([x,y,right-x,bottom-y],r["box"]) > .01 for r in rows)
        prose = len(group) > 1 and len(" ".join(r["source"] for r in group)) > 65
        same_style = all(abs(r["angle"]) < 1 and abs(r["fontSize"]-group[0]["fontSize"]) < .5 for r in group)
        if prose and same_style and not crossed and not other_text and bottom-y < .2:
            master = dict(ordered[0])
            master.update(source=" ".join(r["source"] for r in ordered), box=[x,y,right-x,bottom-y],
                          sourceBoxes=[r["box"][:] for r in ordered], sourceQuads=[q for r in ordered for q in r["sourceQuads"]])
            grouped.append(master)
        else:
            grouped.extend(group)
    rows = grouped
    body = [r for r in rows if r["box"][1] < .84]
    needs_ocr = len(body) < 8 or sum(len(r["source"]) for r in body) < 120
    pix = page.get_pixmap(matrix=fitz.Matrix(3400 / width, 3400 / width), alpha=False)
    pix.save(folder / "original.png")
    if needs_ocr:
        from rapidocr_onnxruntime import RapidOCR
        # Keep low-confidence lines as model evidence instead of silently losing them.
        detected, _ = RapidOCR(det_limit_side_len=1800, intra_op_num_threads=4, inter_op_num_threads=1)(
            str(folder / "original.png"), use_cls=False, text_score=0.0)
        for vertices, value, confidence in detected or []:
            if confidence > .6 and not re.search(r"[A-Za-z]{2,}|[\u4e00-\u9fff]", value):
                continue
            xs, ys = [p[0] for p in vertices], [p[1] for p in vertices]
            b = [min(xs)/pix.width, min(ys)/pix.height, (max(xs)-min(xs))/pix.width, (max(ys)-min(ys))/pix.height]
            if any(overlap(b, r["box"]) > .5 for r in rows):
                continue
            if not value.strip() and b[2] < .02:
                continue
            dx, dy = vertices[1][0]-vertices[0][0], vertices[1][1]-vertices[0][1]
            thickness = min(math.dist(vertices[0],vertices[3]),math.dist(vertices[0],vertices[1])) * height/pix.height
            rows.append({"source": value, "box": b, "fontSize": thickness*.8,
                         "sourceQuads": [[[p[0]/pix.width,p[1]/pix.height] for p in vertices]],
                         "angle": math.degrees(math.atan2(dy,dx)), "origin": "ocr", "confidence": float(confidence)})
    rows.sort(key=lambda r: (r["box"][1], r["box"][0]))
    # Join actual sentence continuations, so percentages and obligations stay together.
    paragraphs = []
    for row in rows:
        b = row["box"]
        row.setdefault("sourceBoxes", [b[:]])
        previous = None
        if row["origin"] == "ocr" and b[1] < .85 and not re.match(r"^\s*(?:\d+[.)]|\([a-zivx]+\))", row["source"], re.I):
            for candidate in reversed(paragraphs):
                a = candidate["box"]
                gap = b[1]-(a[1]+a[3])
                if (candidate["origin"] == "ocr" and len(candidate["source"]) > 55 and
                    not re.search(r"[.;:]\s*$",candidate["source"]) and abs(a[0]-b[0]) < .015 and
                    0 <= gap <= b[3]*.65 and .75 <= candidate.get("fontSize",1)/max(.1,row.get("fontSize",1)) <= 1.3 and
                    not any(other is not row and other is not candidate and
                            a[1]+a[3] <= other["box"][1] < b[1] and
                            abs(other["box"][0]-b[0]) < .03 for other in rows)):
                    previous = candidate
                    break
        if previous:
            a = previous["box"]
            previous["source"] += " " + row["source"]
            previous["sourceBoxes"].extend(row["sourceBoxes"])
            previous.setdefault("sourceQuads", []).extend(row.get("sourceQuads", []))
            previous["box"] = [min(a[0],b[0]),a[1],max(a[0]+a[2],b[0]+b[2])-min(a[0],b[0]),b[1]+b[3]-a[1]]
        else:
            paragraphs.append(row)
    rows = paragraphs
    for i, r in enumerate(rows):
        r["id"] = str(i+1)
    # Explicit crop IDs avoid asking a vision model to infer hundreds of coordinates.
    # All sheets for one page are sent in ONE request, with the full page as context.
    sheets = []
    if needs_ocr:
        image = Image.open(folder / "original.png").convert("RGB")
        label_font = ImageFont.load_default(size=23)
        canvas = Image.new("RGB", (1600, 1500), "white")
        draw = ImageDraw.Draw(canvas)
        cursor = 8
        for row in rows:
            if row["origin"] == "native":
                continue
            x, y, bw, bh = row["box"]
            crop = image.crop((max(0,int(x*image.width)-2),max(0,int(y*image.height)-2),
                               min(image.width,int((x+bw)*image.width)+3),min(image.height,int((y+bh)*image.height)+3)))
            scale = min(38*len(row.get("sourceBoxes", [1]))/max(1,crop.height), 1500/max(1,crop.width), 1200/max(1,crop.height))
            crop = crop.resize((max(1,int(crop.width*scale)),max(1,int(crop.height*scale))),Image.Resampling.LANCZOS)
            line_height = max(36, crop.height+12)
            if cursor+line_height > 1490:
                name = f"anchors-{len(sheets)+1}.jpg"
                canvas.crop((0,0,1600,cursor+4)).save(folder/name, quality=94)
                sheets.append(name)
                canvas = Image.new("RGB", (1600,1500), "white")
                draw = ImageDraw.Draw(canvas)
                cursor = 8
            draw.text((6,cursor+3),row["id"],font=label_font,fill=(20,70,160))
            canvas.paste(crop,(82,cursor+4))
            draw.line((0,cursor+line_height-1,1600,cursor+line_height-1),fill=(225,230,235),width=1)
            cursor += line_height
        if cursor > 8:
            name = f"anchors-{len(sheets)+1}.jpg"
            canvas.crop((0,0,1600,cursor+4)).save(folder/name,quality=94)
            sheets.append(name)
    one.save(folder / "source.pdf", garbage=3, deflate=True)
    one.close()
    result = {"page": number, "width": width, "height": height, "kind": "drawing" if needs_ocr else "text-table",
              "nativeCount": sum(r["origin"] == "native" for r in rows), "ocrUsed": needs_ocr, "rows": rows, "sheets": sheets}
    write_json(folder / "prepared.json", result)
    return {"rows": len(rows), "ocrUsed": needs_ocr}


def overlap(a, b):
    area = max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0])) * max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
    return area / max(.0000001, min(a[2]*a[3], b[2]*b[3]))


def wrap(text, font, size, width):
    lines, current = [], ""
    for char in text:
        if char == "\n" or (current and font.text_length(current + char, fontsize=size) > width):
            lines.append(current)
            current = "" if char == "\n" else char
        else:
            current += char
    if current:
        lines.append(current)
    return lines


def placement_boxes(rows):
    """Split only shallow OCR padding overlaps, never overlapping text cores."""
    boxes = {}
    for row in rows:
        a = row.get("layoutBox") or row["box"]
        left, top, right, bottom = a[0], a[1], a[0]+a[2], a[1]+a[3]
        for other in rows:
            if other["id"] == row["id"]:
                continue
            b = other.get("layoutBox") or other["box"]
            xo = max(0, min(right, b[0]+b[2])-max(left, b[0]))
            yo = max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
            if xo and yo and yo / min(a[3], b[3]) < .4:
                middle = (a[1]+a[3]/2+b[1]+b[3]/2)/2
                if a[1]+a[3]/2 < b[1]+b[3]/2:
                    bottom = min(bottom, middle-.00015)
                else:
                    top = max(top, middle+.00015)
        boxes[row["id"]] = [left, top, right-left, bottom-top] if bottom-top >= a[3]*.8 else list(a)
    return boxes


def fit_region(text, font, source_size, rect, single_line=False):
    # Complete content takes priority over font matching; low size stays visible in QA.
    # The floor is relative to the actual source, rather than a previously shrunk size.
    maximum = min(source_size, rect.height / (font.ascender-font.descender))
    minimum = source_size * .55
    size = maximum
    while size >= minimum - .001:
        wrapped = [text] if single_line else wrap(text, font, size, max(1, rect.width-.5))
        if all(font.text_length(s, fontsize=size) <= rect.width-.49 for s in wrapped) and (font.ascender-font.descender+(len(wrapped)-1)*1.05)*size <= rect.height+.001:
            return wrapped, size
        size -= .15
    return None


def expand_region(b, row, boxes, w, h, rules):
    """Use nearby whitespace, bounded by text anchors and straight table rules."""
    left, top, right, bottom = b[0], b[1], b[0]+b[2], b[1]+b[3]
    limits = [max(0, left-.006), max(0, top-.002), min(1, right+.03), min(1, bottom+.008)]
    for ident, a in boxes.items():
        if ident == row['id']:
            continue
        if max(top, a[1]) < min(bottom, a[1]+a[3]):
            if a[0] >= right: limits[2] = min(limits[2], a[0]-.0004)
            if a[0]+a[2] <= left: limits[0] = max(limits[0], a[0]+a[2]+.0004)
        if max(limits[0], a[0]) < min(limits[2], a[0]+a[2]):
            if a[1] >= bottom: limits[3] = min(limits[3], a[1]-.0004)
            if a[1]+a[3] <= top: limits[1] = max(limits[1], a[1]+a[3]+.0004)
    for p, q, _, _ in rules:
        if abs(p.x-q.x) < .2 and max(top*h,min(p.y,q.y)) < min(bottom*h,max(p.y,q.y)):
            x=p.x/w
            if x >= right: limits[2]=min(limits[2],x-.0004)
            if x <= left: limits[0]=max(limits[0],x+.0004)
        if abs(p.y-q.y) < .2 and max(limits[0]*w,min(p.x,q.x)) < min(limits[2]*w,max(p.x,q.x)):
            y=p.y/h
            if y >= bottom: limits[3]=min(limits[3],y-.0004)
            if y <= top: limits[1]=max(limits[1],y+.0004)
    result=[min(left,limits[0]),min(top,limits[1]),max(right,limits[2])-min(left,limits[0]),max(bottom,limits[3])-min(top,limits[1])]
    if any(ident != row['id'] and overlap(result,a) > .12 for ident,a in boxes.items()):
        return b
    return result


def render(folder, rows_file):
    folder = Path(folder)
    rows = json.loads(Path(rows_file).read_text(encoding="utf-8"))
    doc = fitz.open(folder / "source.pdf")
    page = doc[0]
    w, h = page.rect.width, page.rect.height
    # Use the bundled TrueType CJK font. CFF/OTF subsets can mis-map Chinese glyphs in CAD PDFs.
    font = fitz.Font("cjk")
    lines = []
    for drawing in page.get_drawings():
        for item in drawing["items"]:
            if item[0] == "l":
                p, q = item[1], item[2]
                if (abs(p.x-q.x) < .2 or abs(p.y-q.y) < .2) and abs(p.x-q.x)+abs(p.y-q.y) > 16:
                    lines.append((p, q, drawing.get("color") or (0, 0, 0), (drawing.get("width") or .3)))
            elif item[0] == "re" and drawing.get("fill") is not None:
                r = item[1]
                if 0 < r.width <= 2 and r.height > 16:
                    lines.append((fitz.Point((r.x0+r.x1)/2,r.y0),fitz.Point((r.x0+r.x1)/2,r.y1),drawing["fill"],r.width))
                elif 0 < r.height <= 2 and r.width > 16:
                    lines.append((fitz.Point(r.x0,(r.y0+r.y1)/2),fitz.Point(r.x1,(r.y0+r.y1)/2),drawing["fill"],r.height))
    report, overlays = [], []
    boxes = placement_boxes(rows)
    for row in rows:
        if row.get("disposition") != "translate" or not row.get("target") or row["target"] == row["source"]:
            continue
        b = boxes[row["id"]]
        source_b = row["box"]
        invalid = min(b) < 0 or b[2] <= 0 or b[3] <= 0 or b[0]+b[2] > 1 or b[1]+b[3] > 1
        moved = abs(b[0]-source_b[0]) > .025 or abs(b[1]-source_b[1]) > .025
        collision = any(ident != row["id"] and overlap(b, other) > .12 for ident, other in boxes.items())
        if invalid or moved or collision:
            report.append({"id": row["id"], "flags": ["out-of-page" if invalid else "displaced" if moved else "anchor-collision"], "rendered": False})
            continue
        rect = fitz.Rect(b[0]*w, b[1]*h, (b[0]+b[2])*w, (b[1]+b[3])*h) & page.rect
        flags = []
        if rect.is_empty or rect.width < 2 or rect.height < 2:
            report.append({"id": row["id"], "flags": ["invalid-box"], "rendered": False})
            continue
        angle = row.get("angle",0)
        radians = math.radians(angle)
        direction = fitz.Point(math.cos(radians),math.sin(radians))
        normal = fitz.Point(-direction.y,direction.x)
        rotated = abs(angle) > 5
        center = (rect.tl+rect.br)/2
        if rotated:
            # Inscribed oriented rectangle, so rotated text cannot escape its placement.
            c,s=abs(direction.x),abs(direction.y)
            thickness = row.get("fontSize",min(rect.width,rect.height)*.65)*(font.ascender-font.descender)
            thickness=min(thickness,rect.width/max(s,1e-6)*.95,rect.height/max(c,1e-6)*.95)
            length=min((rect.width-s*thickness)/max(c,1e-6),(rect.height-c*thickness)/max(s,1e-6))
            if c < .0001: length=rect.height
            if s < .0001: length=rect.width
            local = fitz.Rect(0,0,max(1,length),max(1,thickness))
        else:
            local=fitz.Rect(0,0,rect.width,rect.height)
        def measured_size(r):
            size=r.get("fontSize")
            if size and r.get("origin")=="ocr" and r.get("sourceQuads"):
                short_edges=[]
                for quad in r["sourceQuads"]:
                    points=[fitz.Point(p[0]*w,p[1]*h) for p in quad]
                    short_edges.append(min(abs(points[1]-points[0]),abs(points[3]-points[0]))*.8)
                size=min(size,sorted(short_edges)[len(short_edges)//2])
            return size
        nearby = sorted(measured_size(r) for r in rows if r.get("fontSize") and abs(r["box"][1]-source_b[1])<.15)
        source_size = measured_size(row) or (nearby[len(nearby)//2] if nearby else min(rect.height*.65,10))
        if rotated and row.get("origin") == "ocr" and row.get("sourceQuads"):
            # OCR can describe vertical text as a tall axis-aligned rectangle.
            # Once the model resolves its direction, measure across that baseline,
            # not along the whole word, to recover the real character height.
            thicknesses=[]
            for quad in row["sourceQuads"]:
                projections=[p[0]*w*normal.x+p[1]*h*normal.y for p in quad]
                thicknesses.append(max(projections)-min(projections))
            source_size=min(source_size,sorted(thicknesses)[len(thicknesses)//2]*.8)
        source_size *= row.get("fontScale",1)
        text = row["target"].strip()
        compact_unit = bool(re.fullmatch(r"(?:nr|sum|no\.?|nos\.?|m[²³23]?|mm|kg|t)", row["source"].strip(), re.I))
        fitting = fit_region(text, font, source_size, local, single_line=compact_unit)
        if rotated:
            # Recompute the usable length as the font shrinks: holding the original
            # line thickness fixed would incorrectly reject narrow diagonal labels.
            candidate_size=source_size
            fitting=None
            while candidate_size >= source_size*.55-.001:
                thickness=candidate_size*(font.ascender-font.descender)
                c,s=abs(direction.x),abs(direction.y)
                length=min((rect.width-s*thickness)/max(c,1e-6),(rect.height-c*thickness)/max(s,1e-6))
                candidate=fitz.Rect(0,0,max(0,length),thickness)
                if length>1:
                    attempt=fit_region(text,font,candidate_size,candidate)
                    if attempt and attempt[1] >= source_size*.55-.001:
                        fitting=attempt
                        local=candidate
                        break
                candidate_size-=.15
        if not rotated and (fitting is None or fitting[1] < source_size*.8):
            expanded = expand_region(b, row, boxes, w, h, lines)
            larger = fitz.Rect(expanded[0]*w,expanded[1]*h,(expanded[0]+expanded[2])*w,(expanded[1]+expanded[3])*h)
            alternative = fit_region(text,font,source_size,larger,single_line=compact_unit)
            if alternative and (fitting is None or alternative[1] > fitting[1]):
                rect, b, fitting = larger, expanded, alternative
                local=fitz.Rect(0,0,rect.width,rect.height)
                center=(rect.tl+rect.br)/2
                flags.append("whitespace-fit")
        if fitting is None:
            report.append({"id": row["id"], "flags": ["no-fit"], "rendered": False})
            continue
        fitted, size = fitting
        boxes[row['id']] = b
        if size < source_size * .8:
            flags.append("reduced")
        if size < 5:
            flags.append("small")
        # Cover only the original text footprint, then restore intersecting straight rules.
        overlays.append((center, local, fitted, size, angle if rotated else 0))
        quads=row.get("sourceQuads",[])
        masks = quads or [[[sb[0],sb[1]],[sb[0]+sb[2],sb[1]],[sb[0]+sb[2],sb[1]+sb[3]],[sb[0],sb[1]+sb[3]]] for sb in row.get("sourceBoxes", [source_b])]
        for quad in masks:
            points=[fitz.Point(p[0]*w,p[1]*h) for p in quad]
            mask=fitz.Quad(points[0],points[1],points[3],points[2]).rect & page.rect
            page.draw_polyline(points,color=None,fill=(1,1,1),closePath=True,overlay=True)
            for p, q, color, thickness in lines:
                if abs(p.y-q.y) < .2 and mask.y0 <= p.y <= mask.y1:
                    x0, x1 = max(min(p.x,q.x),mask.x0), min(max(p.x,q.x),mask.x1)
                    if x1 > x0:
                        page.draw_line((x0,p.y),(x1,p.y),color=color,width=thickness)
                elif abs(p.x-q.x) < .2 and mask.x0 <= p.x <= mask.x1:
                    y0, y1 = max(min(p.y,q.y),mask.y0), min(max(p.y,q.y),mask.y1)
                    if y1 > y0:
                        page.draw_line((p.x,y0),(p.x,y1),color=color,width=thickness)
        report.append({"id": row["id"], "flags": flags, "rendered": True, "sourceSize": source_size, "size": size, "placement": b,"angle":angle})
    page.insert_font(fontname="translation", fontbuffer=font.buffer)
    for center, local, wrapped, size, angle in overlays:
        radians=math.radians(angle)
        direction=fitz.Point(math.cos(radians),math.sin(radians))
        normal=fitz.Point(-direction.y,direction.x)
        origin=center-direction*(local.width/2)-normal*(local.height/2)
        text_height = (font.ascender-font.descender+(len(wrapped)-1)*1.05)*size
        baseline = (local.height-text_height)/2 + size*font.ascender
        for i, line in enumerate(wrapped):
            point=origin+direction*.25+normal*(baseline+i*size*1.05)
            page.insert_text(point, line, fontsize=size, fontname="translation",morph=(point,fitz.Matrix(-angle)))
    doc.subset_fonts()
    # Save via a distinct temporary file so repeated repairs never use a previous overlay as input.
    tmp = folder / "rendering.pdf"
    doc.save(tmp, garbage=3, deflate=True)
    pix = page.get_pixmap(matrix=fitz.Matrix(2600/w, 2600/w), alpha=False)
    pix.save(folder / "translated.png")
    doc.close()
    tmp.replace(folder / "translated.pdf")
    write_json(folder / "layout.json", report)
    return {"rendered": sum(r["rendered"] for r in report), "retained": sum(not r["rendered"] for r in report)}


def merge(manifest, output):
    paths = json.loads(Path(manifest).read_text(encoding="utf-8"))
    result = fitz.open()
    for value in paths:
        with fitz.open(value) as part:
            result.insert_pdf(part)
    result.save(output, garbage=3, deflate=True)
    result.close()
    return {"pages": len(paths)}


def crops(folder, request):
    folder=Path(folder)
    rows=json.loads(Path(request).read_text(encoding="utf-8"))
    for version in ["source","translated"]:
        with fitz.open(folder/f"{version}.pdf") as doc:
            page=doc[0]
            for i,row in enumerate(rows):
                x,y,w,h=row["box"]
                clip=fitz.Rect((x-.004)*page.rect.width,(y-.004)*page.rect.height,(x+w+.004)*page.rect.width,(y+h+.004)*page.rect.height)&page.rect
                zoom=min(8,1600/max(clip.width,clip.height,1))
                page.get_pixmap(matrix=fitz.Matrix(zoom,zoom),clip=clip,alpha=False).save(folder/f"inspect-{i}-{version}.png")
    return {"regions":len(rows)}


if __name__ == "__main__":
    action = sys.argv[1]
    if action == "prepare":
        result = prepare(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif action == "extract":
        with fitz.open(sys.argv[2]) as source:
            result_doc=fitz.open()
            result_doc.insert_pdf(source,from_page=int(sys.argv[3])-1,to_page=int(sys.argv[3])-1)
            Path(sys.argv[4]).parent.mkdir(parents=True,exist_ok=True)
            result_doc.save(sys.argv[4])
        result={"preservedOriginal":True}
    elif action == "render":
        result = render(sys.argv[2], sys.argv[3])
    elif action == "merge":
        result = merge(sys.argv[2], sys.argv[3])
    elif action == "crops":
        result = crops(sys.argv[2],sys.argv[3])
    elif action == "inspect":
        with fitz.open(sys.argv[2]) as doc:
            result = {"pages": len(doc), "encrypted": doc.needs_pass, "format": doc.metadata.get("format")}
    else:
        raise ValueError("Unknown action")
    print(json.dumps(result, ensure_ascii=False))
