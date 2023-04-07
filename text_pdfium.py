import pathlib
import time

import pypdfium2 as pdfium

mytime = time.perf_counter

filelst = pathlib.Path("files.txt").read_text().splitlines()
filelst.sort()


def ProcessFile(ifile):
    ofile = "pdfium-" + ifile + ".txt"
    out = open(ofile, "wb")
    doc = pdfium.PdfDocument(ifile)
    for page in doc:
        out.write((page.get_textpage().get_text_range() + "\n").encode())
    out.close()


# ==============================================================================
# Main Program
# ==============================================================================
ofile = open("text-speed.csv", "a")
for datei in filelst:
    zeit0 = mytime()
    print("processing:", datei)
    ProcessFile(datei)
    zeit1 = mytime()
    zeit = str(round(zeit1 - zeit0, 2))
    ofile.write(f"pdfium;{datei};{zeit}\n")

ofile.close()
