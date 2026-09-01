// Packaged source for the source-hashed, consistently identified local subprocess.
import AppKit
import Darwin
import Foundation
import ImageIO
import PDFKit
import Vision

enum AdapterFailure: Error {
    case invalidRequest
    case invalidInput
    case invalidConfiguration
    case unreadablePDF
    case renderFailed
}

func boundedInt(
    _ value: Any?,
    default defaultValue: Int,
    minimum: Int,
    maximum: Int
) throws -> Int {
    guard let value else {
        return defaultValue
    }
    guard let number = value as? NSNumber else {
        throw AdapterFailure.invalidConfiguration
    }
    let result = number.intValue
    guard result >= minimum && result <= maximum else {
        throw AdapterFailure.invalidConfiguration
    }
    return result
}

func configuredString(
    _ value: Any?,
    default defaultValue: String,
    allowedValues: Set<String>
) throws -> String {
    guard let value else {
        return defaultValue
    }
    guard let result = value as? String, allowedValues.contains(result) else {
        throw AdapterFailure.invalidConfiguration
    }
    return result
}

func render(_ page: PDFPage, maxEdgePixels: Int) throws -> CGImage {
    let box = page.bounds(for: .mediaBox)
    guard box.width > 0, box.height > 0 else {
        throw AdapterFailure.renderFailed
    }
    let scale = min(
        CGFloat(maxEdgePixels) / box.width,
        CGFloat(maxEdgePixels) / box.height
    )
    let target = CGSize(
        width: max(1, floor(box.width * scale)),
        height: max(1, floor(box.height * scale))
    )
    let image = page.thumbnail(of: target, for: .mediaBox)
    var rectangle = CGRect(origin: .zero, size: image.size)
    guard let rendered = image.cgImage(
        forProposedRect: &rectangle,
        context: nil,
        hints: nil
    ) else {
        throw AdapterFailure.renderFailed
    }
    return rendered
}

func isVisuallyBlank(_ image: CGImage) -> Bool {
    let sampleWidth = image.width
    let sampleHeight = image.height
    guard sampleWidth > 0, sampleHeight > 0,
          sampleWidth * sampleHeight <= 64_000_000 else {
        return false
    }
    // Inspect the rendered pixels without reducing faint or small text to white.
    // Only an exactly uniform RGB image is treated as visually blank.
    var pixels = [UInt8](repeating: 255, count: sampleWidth * sampleHeight * 4)
    let rendered = pixels.withUnsafeMutableBytes { buffer in
        guard let context = CGContext(
            data: buffer.baseAddress,
            width: sampleWidth,
            height: sampleHeight,
            bitsPerComponent: 8,
            bytesPerRow: sampleWidth * 4,
            space: CGColorSpaceCreateDeviceRGB(),
            bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
        ) else {
            return false
        }
        context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
        context.fill(CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight))
        context.interpolationQuality = .none
        context.draw(
            image,
            in: CGRect(x: 0, y: 0, width: sampleWidth, height: sampleHeight)
        )
        return true
    }
    guard rendered else {
        return false
    }
    for offset in stride(from: 4, to: pixels.count, by: 4) {
        if pixels[offset] != pixels[0] || pixels[offset + 1] != pixels[1]
            || pixels[offset + 2] != pixels[2] { return false }
    }
    return true
}

func imageIssue(_ code: String, _ message: String, _ details: [String: Any]) throws {
    try writeResult(["schema_version": "document-files.extraction-result.v1",
                     "completeness": "partial", "units": [], "issues": [[
                        "code": code, "message": message, "severity": "warning",
                        "details": details,
                     ]]])
}

func topLeftBoundingBox(_ box: CGRect) -> [Double] {
    let values = [
        box.minX,
        1.0 - box.maxY,
        box.maxX,
        1.0 - box.minY,
    ]
    return values.map { value in
        Double(min(max(value, 0), 1))
    }
}

#if compiler(>=6.2)
    @available(macOS 26.0, *)
    func topLeftBoundingBox(_ box: NormalizedRect) -> [Double] {
        let rectangle = box.verticallyFlipped().cgRect
        return [
            Double(rectangle.minX),
            Double(rectangle.minY),
            Double(rectangle.maxX),
            Double(rectangle.maxY),
        ]
    }
#endif

func normalizedText(_ text: String?) -> String {
    guard let text else {
        return ""
    }
    return text
        .replacingOccurrences(of: "\r\n", with: "\n")
        .replacingOccurrences(of: "\r", with: "\n")
        .trimmingCharacters(in: .whitespacesAndNewlines)
}

func alphanumericCharacterCount(_ text: String) -> Int {
    text.unicodeScalars.reduce(0) { count, scalar in
        count + (CharacterSet.alphanumerics.contains(scalar) ? 1 : 0)
    }
}

func writeResult(_ result: [String: Any]) throws {
    let data = try JSONSerialization.data(
        withJSONObject: result,
        options: [.sortedKeys, .withoutEscapingSlashes]
    )
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data([0x0A]))
}

func appendWithinBudget(
    _ candidates: [[String: Any]],
    to units: inout [[String: Any]],
    totalContentCharacters: inout Int,
    maxUnits: Int,
    maxUnitContentCharacters: Int,
    maxTotalContentCharacters: Int
) -> Bool {
    let candidateCharacters = candidates.compactMap { ($0["content"] as? String)?.count }.reduce(0, +)
    guard units.count <= maxUnits - candidates.count,
          totalContentCharacters <= maxTotalContentCharacters - candidateCharacters,
          candidates.allSatisfy({ ($0["content"] as? String)?.count ?? (maxUnitContentCharacters + 1) <= maxUnitContentCharacters })
    else { return false }
    for candidate in candidates {
        guard
            let content = candidate["content"] as? String,
            content.count <= maxUnitContentCharacters,
            units.count < maxUnits,
            totalContentCharacters <= maxTotalContentCharacters - content.count
        else {
            return false
        }
        units.append(candidate)
        totalContentCharacters += content.count
    }
    return true
}

func averageConfidence(_ lines: [RecognizedTextObservation]) -> Double? {
    let values = lines.compactMap { line in
        line.topCandidates(1).first.map { Double($0.confidence) }
    }
    guard !values.isEmpty else {
        return nil
    }
    let average = values.reduce(0, +) / Double(values.count)
    return min(max(average, 0), 1)
}

#if compiler(>=6.2)
    @available(macOS 26.0, *)
    func recognizeStructuredPage(
        _ image: CGImage,
        page: Int,
        languages: [String]
    ) async throws -> [[String: Any]] {
        var recognition = RecognizeDocumentsRequest()
        recognition.textRecognitionOptions.recognitionLanguages = languages.map {
            Locale.Language(identifier: $0)
        }
        recognition.textRecognitionOptions.useLanguageCorrection = true
        recognition.textRecognitionOptions.automaticallyDetectLanguage = true
        guard let document = try await recognition.perform(on: image).first?.document else {
            return []
        }

        var units: [[String: Any]] = []
        var structurallyOwnedLines: Set<UUID> = []
        for (tableIndex, table) in document.tables.enumerated() {
            for (rowIndex, row) in table.rows.enumerated() {
                for (cellIndex, cell) in row.enumerated() {
                    let text = normalizedText(cell.content.text.transcript)
                    guard !text.isEmpty else {
                        continue
                    }
                    let lines = cell.content.text.lines
                    structurallyOwnedLines.formUnion(lines.map(\.uuid))
                    var unit: [String: Any] = [
                        "unit_type": "table_cell",
                        "structure_path": [
                            "page": page,
                            "table": tableIndex + 1,
                            "row": rowIndex + 1,
                            "cell": cellIndex + 1,
                            "row_start": cell.rowRange.lowerBound + 1,
                            "row_end": cell.rowRange.upperBound,
                            "column_start": cell.columnRange.lowerBound + 1,
                            "column_end": cell.columnRange.upperBound,
                        ],
                        "content": text,
                        "derivation_method": "ocr",
                        "geometry": [
                            "coordinate_system": "top_left_normalized",
                            "bbox": topLeftBoundingBox(cell.content.boundingRegion.boundingBox),
                        ],
                        "quality_flags": [
                            "ocr",
                            "structured_ocr",
                            "table_cell",
                            "reading_order_unverified",
                        ],
                        "issues": [],
                    ]
                    if let confidence = averageConfidence(lines) {
                        unit["confidence"] = confidence
                    }
                    units.append(unit)
                }
            }
        }

        for (paragraphIndex, paragraph) in document.paragraphs.enumerated() {
            let lineIdentifiers = Set(paragraph.lines.map(\.uuid))
            if !lineIdentifiers.isEmpty && lineIdentifiers.isSubset(of: structurallyOwnedLines) {
                continue
            }
            let text = normalizedText(paragraph.transcript)
            guard !text.isEmpty else {
                continue
            }
            var unit: [String: Any] = [
                "unit_type": "paragraph",
                "structure_path": [
                    "page": page,
                    "paragraph": paragraphIndex + 1,
                ],
                "content": text,
                "derivation_method": "ocr",
                "geometry": [
                    "coordinate_system": "top_left_normalized",
                    "bbox": topLeftBoundingBox(paragraph.boundingRegion.boundingBox),
                ],
                "quality_flags": ["ocr", "structured_ocr", "reading_order_unverified"],
                "issues": [],
            ]
            if let confidence = averageConfidence(paragraph.lines) {
                unit["confidence"] = confidence
            }
            units.append(unit)
        }
        return units
    }
#endif

func recognizeTextPage(
    _ image: CGImage,
    page: Int,
    languages: [String]
) throws -> [[String: Any]] {
    let recognition = VNRecognizeTextRequest()
    recognition.recognitionLevel = .accurate
    recognition.recognitionLanguages = languages
    recognition.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([recognition])
    var units: [[String: Any]] = []
    for (visionIndex, observation) in (recognition.results ?? []).enumerated() {
        guard let candidate = observation.topCandidates(1).first else {
            continue
        }
        let text = normalizedText(candidate.string)
        guard !text.isEmpty else {
            continue
        }
        units.append([
            "unit_type": "page_region",
            "structure_path": [
                "page": page,
                "vision_index": visionIndex,
            ],
            "content": text,
            "derivation_method": "ocr",
            "geometry": [
                "coordinate_system": "top_left_normalized",
                "bbox": topLeftBoundingBox(observation.boundingBox),
            ],
            "confidence": Double(candidate.confidence),
            "quality_flags": ["ocr", "reading_order_unverified"],
            "issues": [],
        ])
    }
    return units
}

enum ThinImageFailure: String, Error {
    case dimensions
    case canvas_creation
    case geometry_mapping
}

func recognizeThinImage(
    _ image: CGImage,
    languages: [String]
) throws -> [[String: Any]] {
    // Retry only a decoded image rejected by Vision. Padding does not establish
    // that a thin picture is decorative, blank, or free of text.
    guard min(image.width, image.height) < 32 else { throw ThinImageFailure.dimensions }
    let width = max(32, image.width)
    let height = max(32, image.height)
    let left = (width - image.width) / 2
    let bottom = (height - image.height) / 2
    let top = height - bottom - image.height
    guard let context = CGContext(
        data: nil, width: width, height: height, bitsPerComponent: 8,
        bytesPerRow: width * 4, space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { throw ThinImageFailure.canvas_creation }
    context.setFillColor(red: 1, green: 1, blue: 1, alpha: 1)
    context.fill(CGRect(x: 0, y: 0, width: width, height: height))
    context.interpolationQuality = .none
    context.draw(image, in: CGRect(x: left, y: bottom,
                                  width: image.width, height: image.height))
    guard let padded = context.makeImage() else { throw ThinImageFailure.canvas_creation }
    var units = try recognizeTextPage(padded, page: 1, languages: languages)
    for index in units.indices {
        guard var geometry = units[index]["geometry"] as? [String: Any],
              let box = geometry["bbox"] as? [Double], box.count == 4 else {
            throw ThinImageFailure.geometry_mapping
        }
        let mapped = [
            (box[0] * Double(width) - Double(left)) / Double(image.width),
            (box[1] * Double(height) - Double(top)) / Double(image.height),
            (box[2] * Double(width) - Double(left)) / Double(image.width),
            (box[3] * Double(height) - Double(top)) / Double(image.height),
        ]
        // Do not clip a candidate extending into the added margin and silently
        // claim that its whole text belongs to the stored source pixels.
        guard mapped.allSatisfy({ $0.isFinite && $0 >= 0 && $0 <= 1 }),
              mapped[0] < mapped[2], mapped[1] < mapped[3] else {
            throw ThinImageFailure.geometry_mapping
        }
        geometry["bbox"] = mapped
        units[index]["geometry"] = geometry
        var location = units[index]["structure_path"] as? [String: Any] ?? [:]
        location["recognition_padding"] = [
            "canvas_pixels": [width, height],
            "source_pixels": [image.width, image.height],
            "source_offset_top_left": [left, top],
        ]
        units[index]["structure_path"] = location
    }
    return units
}

func runAdapter() async throws {
    guard
        let requestLine = readLine(),
        let requestData = requestLine.data(using: .utf8),
        let request = try JSONSerialization.jsonObject(with: requestData) as? [String: Any],
        request["schema_version"] as? String == "document-files.extraction-request.v1",
        request["operation"] as? String == "extract",
        let input = request["input"] as? [String: Any],
        input["kind"] as? String == "read_only_file_descriptor",
        let formatID = input["format_id"] as? String,
        ["pdf", "image"].contains(formatID),
        let fileDescriptor = input["file_descriptor"] as? Int,
        fileDescriptor >= 0,
        fileDescriptor <= Int(Int32.max),
        let path = input["path"] as? String,
        path == "/dev/fd/\(fileDescriptor)",
        fcntl(Int32(fileDescriptor), F_GETFD) != -1
    else {
        throw AdapterFailure.invalidRequest
    }

    let config = request["config"] as? [String: Any] ?? [:]
    let selectedPages = config["selected_pages"] as? [Int]
    if let selectedPages {
        guard !selectedPages.isEmpty, selectedPages.count <= 2_000,
              selectedPages.allSatisfy({ $0 >= 1 && $0 <= 1_000_000 }) else {
            throw AdapterFailure.invalidConfiguration
        }
    }
    let maxPages = try boundedInt(
        config["max_pages"],
        default: 200,
        minimum: 1,
        maximum: 2_000
    )
    let pageStart = try boundedInt(
        config["page_start"], default: 1, minimum: 1, maximum: 1_000_000
    )
    let maxEdgePixels = try boundedInt(
        config["max_edge_pixels"],
        default: 3_000,
        minimum: 512,
        maximum: 8_192
    )
    let languages = config["recognition_languages"] as? [String] ?? ["ko-KR", "en-US"]
    guard !languages.isEmpty, languages.count <= 8 else {
        throw AdapterFailure.invalidConfiguration
    }
    let ocrScope = try configuredString(
        config["ocr_scope"],
        default: "hybrid",
        allowedValues: ["hybrid", "all_pages", "textless_pages"]
    )
    let nativeTextMinAlphanumericCharacters = try boundedInt(
        config["native_text_min_alphanumeric_characters"],
        default: 32,
        minimum: 1,
        maximum: 10_000
    )
    let budgets = request["budgets"] as? [String: Any] ?? [:]
    let maxUnits = try boundedInt(
        budgets["max_units"],
        default: 250_000,
        minimum: 1,
        maximum: 1_000_000
    )
    let maxUnitContentCharacters = try boundedInt(
        budgets["max_unit_content_chars"],
        default: 5_000_000,
        minimum: 1,
        maximum: 50_000_000
    )
    let maxTotalContentCharacters = try boundedInt(
        budgets["max_total_content_chars"],
        default: 150_000_000,
        minimum: 1,
        maximum: 500_000_000
    )

    let inputURL = URL(fileURLWithPath: path)
    if formatID == "image" {
        // /dev/fd descriptors share an offset. Inspect magic without moving the
        // decoder's input position or closing another handle to the same file.
        var header = [UInt8](repeating: 0, count: 44)
        let prefixCount = header.withUnsafeMutableBytes {
            pread(Int32(fileDescriptor), $0.baseAddress, 44, 0)
        }
        guard prefixCount >= 0 else { throw AdapterFailure.invalidInput }
        header = Array(header.prefix(prefixCount))
        let emf = header.count >= 44 && Array(header[40..<44]) == [32, 69, 77, 70]
        let wmf = header.count >= 18 && (
            Array(header[0..<4]) == [215, 205, 198, 154] ||
            ((header[0] == 1 || header[0] == 2) && header[1] == 0 &&
             header[2] == 9 && header[3] == 0 && header[4] == 0 &&
             (header[5] == 1 || header[5] == 3))
        )
        if emf || wmf {
            try imageIssue("image_format_unsupported",
                           "This image decoder has no verified EMF/WMF rasterization path.",
                           ["stage": "format_detection", "format": emf ? "emf" : "wmf",
                            "retryable": false])
            return
        }
        guard let source = CGImageSourceCreateWithURL(inputURL as CFURL, nil) else {
            try writeResult(["schema_version": "document-files.extraction-result.v1",
                             "completeness": "partial", "units": [], "issues": [[
                "code": "image_format_unsupported", "severity": "warning",
                "message": "ImageIO cannot decode this embedded image format.",
            ]]])
            return
        }
        guard let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
              let width = properties[kCGImagePropertyPixelWidth] as? NSNumber,
              let height = properties[kCGImagePropertyPixelHeight] as? NSNumber,
              width.doubleValue > 0, height.doubleValue > 0
        else {
            try imageIssue("image_decode_failed", "Image dimensions could not be decoded.",
                           ["stage": "image_properties", "retryable": false])
            return
        }
        if width.doubleValue * height.doubleValue > 64_000_000 {
            try imageIssue("image_pixel_budget_exceeded",
                           "The embedded image exceeds the decoded pixel budget.",
                           ["stage": "image_properties", "budget": "max_source_pixels",
                            "limit": 64_000_000, "unit": "pixels",
                            "observed": width.doubleValue * height.doubleValue,
                            "source_width": width, "source_height": height,
                            "retryable": false])
            return
        }
        guard let image = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                  kCGImageSourceCreateThumbnailFromImageAlways: true,
                  kCGImageSourceCreateThumbnailWithTransform: true,
                  kCGImageSourceThumbnailMaxPixelSize: maxEdgePixels,
              ] as CFDictionary)
        else {
            try imageIssue("image_decode_failed", "Image pixels could not be decoded.",
                           ["stage": "image_thumbnail", "retryable": false])
            return
        }
        let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
        var recognitionImage = image
        var sourceCrop: [Double]? = nil
        if let configuredCrop = config["source_crop_bbox"] {
            guard let crop = configuredCrop as? [Double], crop.count == 4,
                  crop.allSatisfy({ $0.isFinite && $0 >= 0 && $0 <= 1 }),
                  crop[0] < crop[2], crop[1] < crop[3] else {
                throw AdapterFailure.invalidConfiguration
            }
            // Do not guess how a source crop interacts with EXIF transforms.
            guard orientation == 1 else {
                try imageIssue("image_crop_placement_unresolved",
                               "The source crop has an unverified image orientation.",
                               ["stage": "source_crop", "orientation": orientation,
                                "retryable": false])
                return
            }
            let left = ceil(crop[0] * Double(image.width))
            let top = ceil(crop[1] * Double(image.height))
            let right = floor(crop[2] * Double(image.width))
            let bottom = floor(crop[3] * Double(image.height))
            guard right > left, bottom > top,
                  let cropped = image.cropping(to: CGRect(
                    x: left, y: top, width: right - left, height: bottom - top
                  )) else {
                try imageIssue("image_crop_placement_unresolved",
                               "The visible crop has no pixels at the bounded recognition resolution.",
                               ["stage": "source_crop", "retryable": false])
                return
            }
            recognitionImage = cropped
            sourceCrop = [left / Double(image.width), top / Double(image.height),
                          right / Double(image.width), bottom / Double(image.height)]
        }
        var recognized: [[String: Any]]
        var paddingUsed = false
        do {
            recognized = try recognizeTextPage(recognitionImage, page: 1, languages: languages)
        } catch {
            let failure = error as NSError
            let paddingEligible = failure.domain == VNErrorDomain &&
                failure.code == VNErrorCode.invalidImage.rawValue &&
                min(recognitionImage.width, recognitionImage.height) < 32
            do {
                guard paddingEligible else { throw failure }
                recognized = try recognizeThinImage(recognitionImage, languages: languages)
                paddingUsed = true
            } catch {
                var details: [String: Any] = [
                    "stage": "text_recognition", "error_domain": failure.domain,
                    "error_code": failure.code, "retryable": false,
                    "source_width": width, "source_height": height,
                    "recognition_width": recognitionImage.width,
                    "recognition_height": recognitionImage.height,
                    "thin_image_padding_attempted": paddingEligible,
                ]
                if paddingEligible {
                    details["padding_failure_stage"] =
                        (error as? ThinImageFailure)?.rawValue ?? "text_recognition"
                    if !(error is ThinImageFailure) {
                        let paddingError = error as NSError
                        details["padding_error_domain"] = paddingError.domain
                        details["padding_error_code"] = paddingError.code
                    }
                }
                try imageIssue("image_ocr_failed", "Vision could not recognize this image.",
                               details)
                return
            }
        }
        for index in recognized.indices {
            var location = recognized[index]["structure_path"] as? [String: Any] ?? [:]
            location["source_image_orientation"] = orientation
            if let crop = sourceCrop,
               var geometry = recognized[index]["geometry"] as? [String: Any],
               let box = geometry["bbox"] as? [Double], box.count == 4 {
                geometry["bbox"] = [
                    crop[0] + box[0] * (crop[2] - crop[0]),
                    crop[1] + box[1] * (crop[3] - crop[1]),
                    crop[0] + box[2] * (crop[2] - crop[0]),
                    crop[1] + box[3] * (crop[3] - crop[1]),
                ]
                recognized[index]["geometry"] = geometry
                location["source_crop_bbox"] = crop
            }
            recognized[index]["structure_path"] = location
        }
        var units: [[String: Any]] = []
        var characters = 0
        let withinBudget = appendWithinBudget(recognized, to: &units, totalContentCharacters: &characters,
                                 maxUnits: maxUnits, maxUnitContentCharacters: maxUnitContentCharacters,
                                 maxTotalContentCharacters: maxTotalContentCharacters)
        var issues: [[String: Any]] = !withinBudget || units.isEmpty ? [[
            "code": withinBudget ? "image_without_text" : "image_text_budget_exceeded",
            "severity": "warning",
            "message": withinBudget ? "Vision found no text in the embedded image." : "Image OCR exceeds the bounded result size.",
        ]] : []
        if paddingUsed {
            issues.append([
                "code": "image_ocr_padding_observed", "severity": "info",
                "message": "Vision processed the decoded image with a bounded margin after rejecting its dimensions.",
                "details": ["stage": "text_recognition_padding", "padding_min_edge": 32,
                            "recognition_width": recognitionImage.width,
                            "recognition_height": recognitionImage.height,
                            "source_width": width, "source_height": height],
            ])
        }
        try writeResult(["schema_version": "document-files.extraction-result.v1",
                         "completeness": units.isEmpty ? "partial" : "complete",
                         "units": units, "issues": issues])
        return
    }
    guard let document = PDFDocument(url: inputURL) else {
        throw AdapterFailure.unreadablePDF
    }

    var units: [[String: Any]] = []
    var totalContentCharacters = 0
    var issues: [[String: Any]] = []
    guard pageStart <= document.pageCount + 1 else { throw AdapterFailure.invalidConfiguration }
    let pageEnd = min(document.pageCount, pageStart - 1 + maxPages)
    var processedEnd = pageStart - 1
    let timeout = (budgets["timeout_seconds"] as? NSNumber)?.doubleValue ?? 180
    let deadline = Date().addingTimeInterval(max(0.01, timeout * 0.8))
    var stopReason = "page_limit"

    for pageIndex in (pageStart - 1)..<pageEnd {
        if Date() >= deadline { stopReason = "time_budget"; break }
        let unitsBeforePage = units.count
        let charactersBeforePage = totalContentCharacters
        processedEnd = pageIndex + 1
        if let selectedPages, !selectedPages.contains(pageIndex + 1) { continue }
        guard let page = document.page(at: pageIndex) else {
            issues.append([
                "code": "pdf_page_unavailable",
                "message": "PDFKit could not open one page.",
                "severity": "warning",
                "details": ["page": pageIndex + 1],
            ])
            continue
        }

        let nativeText = normalizedText(page.string)
        if !nativeText.isEmpty {
            if !appendWithinBudget(
                [[
                    "unit_type": "page",
                    "structure_path": ["page": pageIndex + 1],
                    "content": nativeText,
                    "derivation_method": "native_text",
                    "quality_flags": ["reading_order_unverified"],
                    "issues": [],
                ]],
                to: &units,
                totalContentCharacters: &totalContentCharacters,
                maxUnits: maxUnits,
                maxUnitContentCharacters: maxUnitContentCharacters,
                maxTotalContentCharacters: maxTotalContentCharacters
            ) {
                units.removeLast(units.count - unitsBeforePage)
                totalContentCharacters = charactersBeforePage
                processedEnd = pageIndex
                stopReason = "result_budget"
                issues.append([
                    "code": "adapter_budget_reached",
                    "message": "PDF extraction stopped at the configured result budget.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
                break
            }
        }

        let shouldRunOCR: Bool
        switch ocrScope {
        case "all_pages":
            shouldRunOCR = true
        case "textless_pages":
            shouldRunOCR = nativeText.isEmpty
        default:
            shouldRunOCR = (
                alphanumericCharacterCount(nativeText)
                    < nativeTextMinAlphanumericCharacters
            )
        }
        if !shouldRunOCR {
            continue
        }
        do {
            let image = try autoreleasepool {
                try render(page, maxEdgePixels: maxEdgePixels)
            }
            if nativeText.isEmpty && isVisuallyBlank(image) {
                issues.append([
                    "code": "pdf_page_visually_blank", "severity": "info",
                    "message": "The rendered page has uniform color; native fallback may verify it.",
                    "details": ["page": pageIndex + 1],
                ])
                continue
            }
            var recognized: [[String: Any]] = []
#if compiler(>=6.2)
                if #available(macOS 26.0, *) {
                    do {
                        recognized = try await recognizeStructuredPage(
                            image,
                            page: pageIndex + 1,
                            languages: languages
                        )
                        if recognized.isEmpty {
                            recognized = try recognizeTextPage(
                                image, page: pageIndex + 1, languages: languages
                            )
                            if !recognized.isEmpty {
                                issues.append([
                                    "code": "pdf_empty_structured_ocr_fallback", "severity": "info",
                                    "message": "Text-region OCR recovered an empty structured recognition result.",
                                    "details": ["page": pageIndex + 1],
                                ])
                            }
                        }
                    } catch {
                        issues.append([
                            "code": "pdf_structured_ocr_fallback",
                            "message": (
                                "Structured Vision recognition failed; text-region OCR was used "
                                    + "for one page."
                            ),
                            "severity": "warning",
                            "details": ["page": pageIndex + 1],
                        ])
                        recognized = try recognizeTextPage(
                            image,
                            page: pageIndex + 1,
                            languages: languages
                        )
                    }
                } else {
                    recognized = try recognizeTextPage(
                        image,
                        page: pageIndex + 1,
                        languages: languages
                    )
                }
#else
                recognized = try recognizeTextPage(
                    image,
                    page: pageIndex + 1,
                    languages: languages
                )
#endif
            if !appendWithinBudget(
                recognized,
                to: &units,
                totalContentCharacters: &totalContentCharacters,
                maxUnits: maxUnits,
                maxUnitContentCharacters: maxUnitContentCharacters,
                maxTotalContentCharacters: maxTotalContentCharacters
            ) {
                units.removeLast(units.count - unitsBeforePage)
                totalContentCharacters = charactersBeforePage
                processedEnd = pageIndex
                stopReason = "result_budget"
                issues.append([
                    "code": "adapter_budget_reached",
                    "message": "PDF extraction stopped at the configured result budget.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
                break
            }
            if nativeText.isEmpty && recognized.isEmpty {
                issues.append([
                    "code": "pdf_page_without_text",
                    "message": "Neither PDFKit nor Vision found text on one page.",
                    "severity": "warning",
                    "details": ["page": pageIndex + 1],
                ])
            }
        } catch {
            issues.append([
                "code": "pdf_ocr_page_failed",
                "message": "Vision OCR could not process one PDF page.",
                "severity": "warning",
                "details": ["page": pageIndex + 1],
            ])
        }
    }

    issues.append([
        "code": "pdf_page_range_observed",
        "message": "The adapter observed this contiguous original page range.",
        "severity": "info",
        "details": ["page_start": pageStart, "page_end": processedEnd,
                    "document_pages": document.pageCount],
    ])
    if processedEnd < pageStart && stopReason == "result_budget" {
        issues.append([
            "code": "pdf_page_unit_budget_exhausted", "severity": "warning",
            "message": "One original page exceeds the extraction result budget.",
            "details": ["next_page": pageStart, "document_pages": document.pageCount],
        ])
    } else if processedEnd < document.pageCount {
        issues.append([
            "code": "pdf_page_range_pending",
            "message": "Further original pages remain for a bounded continuation.",
            "severity": "warning",
            "details": ["next_page": processedEnd + 1, "document_pages": document.pageCount,
                        "reason": stopReason],
        ])
    }

    let hasIncompleteIssue = issues.contains { issue in
        guard let severity = issue["severity"] as? String else {
            return false
        }
        return severity == "warning" || severity == "error"
    }

    try writeResult([
        "schema_version": "document-files.extraction-result.v1",
        "completeness": units.isEmpty || hasIncompleteIssue ? "partial" : "complete",
        "units": units,
        "issues": issues,
    ])
}

@main
struct CorpusPDFVisionAdapter {
    static func main() async {
        do {
            try await runAdapter()
        } catch {
            FileHandle.standardError.write(Data("PDF extraction adapter failed.\n".utf8))
            exit(2)
        }
    }
}
