import os
import pathlib
import time

import pypdfium2 as pdfium

filelst = pathlib.Path("files.txt").read_text().splitlines()
filelst.sort()


def ProcessFile(datei):
    print("processing:", datei)
    doc = pdfium.PdfDocument(datei)
    for i in range(len(doc)):
        page = doc[i]
        bitmap = page.render(scale=150 / 72)
        img = bitmap.to_pil()
        img.save(os.path.join("images", "pdfium-%s.png" % i))
        bitmap = None
        img = None
    doc.close()
    return


ofile = open("render-speed.csv", "a")
for datei in filelst:
    zeit0 = time.perf_counter()
    ProcessFile(datei)
    zeit1 = time.perf_counter()
    zeit = str(round(zeit1 - zeit0, 2))
    ofile.write(f"pdfium;{datei};{zeit}\n")

ofile.close()
