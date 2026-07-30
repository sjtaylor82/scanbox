Run PDF2WORD.exe.

This is the hybrid build. Users do not need to install Python.

By default the app creates accessible text using the C# converter.
If "Attempt OCR on scanned pages" is checked, bundled Tesseract OCR is used when available. Windows OCR is used as a fallback.
If "Document has tables" is checked, the bundled PyMuPDF helper in system\python is used for better table extraction.
"Document has tables" is unavailable while OCR is checked.

Keep the whole system folder beside PDF2WORD.exe. It contains the required support files, including PyMuPDF and Tesseract.
