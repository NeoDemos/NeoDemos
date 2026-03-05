import Foundation
import Vision
import AppKit
import PDFKit

func ocrPDF(path: String) {
    print("DEBUG: Starting OCR for \(path)")
    let url = URL(fileURLWithPath: path)
    guard let document = PDFDocument(url: url) else {
        fputs("Error: Could not open PDF at \(path)\n", stderr)
        exit(1)
    }

    print("DEBUG: PDF opened. Page count: \(document.pageCount)")
    var fullText = ""

    for i in 0..<document.pageCount {
        print("DEBUG: Processing page \(i)")
        guard let page = document.page(at: i) else { 
            print("DEBUG: Could not get page \(i)")
            continue 
        }
        
        let rect = page.bounds(for: .mediaBox)
        print("DEBUG: Page rect: \(rect)")
        
        // Use thumbnail for simpler rendering
        let size = NSSize(width: rect.width * 2, height: rect.height * 2)
        let image = page.thumbnail(of: size, for: .mediaBox)
        
        guard let tiffData = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiffData),
              let cgImage = bitmap.cgImage else {
            print("DEBUG: Failed to get CGImage for page \(i)")
            continue
        }

        print("DEBUG: Image created. Running Vision request...")
        
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.recognitionLanguages = ["nl", "en"]
        
        let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
        do {
            try handler.perform([request])
            guard let observations = request.results else { 
                print("DEBUG: No results for page \(i)")
                continue 
            }
            print("DEBUG: Found \(observations.count) observations")
            for observation in observations {
                guard let candidate = observation.topCandidates(1).first else { continue }
                fullText += candidate.string + " "
            }
        } catch {
            print("DEBUG: OCR failed: \(error)")
            continue
        }
        fullText += "\n"
    }

    print("\n--- OCR RESULT START ---\n")
    print(fullText)
    print("\n--- OCR RESULT END ---\n")
}

let args = CommandLine.arguments
if args.count < 2 {
    print("Usage: swift ocr_pdf.swift <path_to_pdf>")
    exit(1)
}

ocrPDF(path: args[1])
