import pathlib
import sys
import time

import pypdfium2 as pdfium

filelst = pathlib.Path("files.txt").read_text().splitlines()

ofile = open("copy-speed.csv", "a")
filelst.sort()
for datei in filelst:
    zeit0 = time.perf_counter()
    print("processing", datei)
    doc = pdfium.PdfDocument(datei)
    doc.save("pdfium-" + datei)
    zeit1 = time.perf_counter()
    zeit = str(round(zeit1 - zeit0, 2))
    ofile.write(f"pdfium;{datei};{zeit}\n")
ofile.close()
