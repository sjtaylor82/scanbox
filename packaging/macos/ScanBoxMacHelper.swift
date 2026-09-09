import AppKit
import ApplicationServices
import AVFoundation
import Carbon.HIToolbox
import CoreImage
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ImageCaptureCore
import ImageIO
import PDFKit
import ScreenCaptureKit
import UniformTypeIdentifiers
import Vision

enum HelperError: LocalizedError {
    case usage
    case hotKeyRegistrationFailed(OSStatus)
    case permissionDenied
    case noFrontmostApplication
    case noActiveWindow
    case imageWriteFailed
    case cameraUnavailable

    var errorDescription: String? {
        switch self {
        case .usage:
            return "Usage: scanbox-macos-helper listen OUTPUT_DIRECTORY | "
                + "list-scanners | list-cameras | "
                + "scan SCANNER_ID OUTPUT_DIRECTORY | ocr IMAGE_PATH | orientation IMAGE_PATH"
        case .hotKeyRegistrationFailed(let status):
            return "A global shortcut could not be registered (error \(status))."
        case .permissionDenied:
            return "Screen Recording permission was not granted."
        case .noFrontmostApplication:
            return "macOS could not identify the frontmost application."
        case .noActiveWindow:
            return "The frontmost application has no capturable normal window."
        case .imageWriteFailed:
            return "The captured window image could not be saved."
        case .cameraUnavailable:
            return "The selected camera is not available."
        }
    }
}

private func writePNG(_ image: CGImage, to outputURL: URL) throws {
    guard let destination = CGImageDestinationCreateWithURL(
        outputURL as CFURL,
        UTType.png.identifier as CFString,
        1,
        nil
    ) else {
        throw HelperError.imageWriteFailed
    }
    CGImageDestinationAddImage(destination, image, nil)
    guard CGImageDestinationFinalize(destination) else {
        throw HelperError.imageWriteFailed
    }
}

private func recognisedText(in image: CGImage) throws -> String {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    let observations = (request.results ?? []).sorted { left, right in
        if abs(left.boundingBox.midY - right.boundingBox.midY) > 0.015 {
            return left.boundingBox.midY > right.boundingBox.midY
        }
        return left.boundingBox.minX < right.boundingBox.minX
    }
    return observations.compactMap {
        $0.topCandidates(1).first?.string
    }.joined(separator: "\n")
}

private func recognisedTextRotation(in image: CGImage) throws -> Int? {
    let request = VNRecognizeTextRequest()
    request.recognitionLevel = .accurate
    request.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([request])
    var horizontal = 0.0
    var vertical = 0.0
    var weight = 0.0
    for observation in request.results ?? [] {
        guard let candidate = observation.topCandidates(1).first else { continue }
        let value = candidate.string
        guard value.count >= 3 else { continue }
        let firstStart = value.startIndex
        let firstEnd = value.index(after: firstStart)
        let lastEnd = value.endIndex
        let lastStart = value.index(before: lastEnd)
        guard let first = try? candidate.boundingBox(for: firstStart..<firstEnd),
              let last = try? candidate.boundingBox(for: lastStart..<lastEnd)
        else { continue }
        let dx = last.boundingBox.midX - first.boundingBox.midX
        let dy = last.boundingBox.midY - first.boundingBox.midY
        let distance = hypot(dx, dy)
        guard distance > 0.01 else { continue }
        let lineWeight = min(Double(value.count), 80.0)
        horizontal += (dx / distance) * lineWeight
        vertical += (dy / distance) * lineWeight
        weight += lineWeight
    }
    guard weight >= 12.0, hypot(horizontal, vertical) / weight >= 0.55 else {
        return nil
    }
    if abs(horizontal) >= abs(vertical) {
        return horizontal >= 0 ? 0 : 180
    }
    return vertical >= 0 ? 90 : 270
}

final class CameraService: NSObject, AVCaptureVideoDataOutputSampleBufferDelegate {
    enum Operation {
        case still(URL)
        case faceAlign
    }

    private let operation: Operation
    private let deviceIndex: Int
    private let session = AVCaptureSession()
    private let context = CIContext(options: nil)
    private var frameCount = 0
    private var lastFaceCheck = Date.distantPast
    private var finished = false

    init(deviceIndex: Int, operation: Operation) {
        self.deviceIndex = deviceIndex
        self.operation = operation
    }

    func start() throws {
        switch AVCaptureDevice.authorizationStatus(for: .video) {
        case .authorized:
            try startSession()
        case .notDetermined:
            AVCaptureDevice.requestAccess(for: .video) { granted in
                if granted {
                    do {
                        try self.startSession()
                    } catch {
                        self.fail(error.localizedDescription)
                    }
                } else {
                    self.fail("Camera access was not granted.")
                }
            }
        default:
            throw HelperError.permissionDenied
        }
    }

    private func startSession() throws {
        let devices = AVCaptureDevice.devices(for: .video)
        guard devices.indices.contains(deviceIndex) else {
            throw HelperError.cameraUnavailable
        }
        let input = try AVCaptureDeviceInput(device: devices[deviceIndex])
        let output = AVCaptureVideoDataOutput()
        output.alwaysDiscardsLateVideoFrames = true
        output.videoSettings = [
            kCVPixelBufferPixelFormatTypeKey as String:
                kCVPixelFormatType_32BGRA
        ]
        output.setSampleBufferDelegate(
            self,
            queue: DispatchQueue(label: "au.com.scanbox.camera")
        )
        session.beginConfiguration()
        guard session.canAddInput(input), session.canAddOutput(output) else {
            session.commitConfiguration()
            throw HelperError.cameraUnavailable
        }
        session.addInput(input)
        session.addOutput(output)
        session.commitConfiguration()
        session.startRunning()
    }

    func captureOutput(
        _ output: AVCaptureOutput,
        didOutput sampleBuffer: CMSampleBuffer,
        from connection: AVCaptureConnection
    ) {
        guard !finished,
              let pixelBuffer = CMSampleBufferGetImageBuffer(sampleBuffer)
        else { return }
        frameCount += 1

        switch operation {
        case .still(let outputURL):
            // Give autofocus and exposure several frames to settle.
            guard frameCount >= 15 else { return }
            do {
                let image = CIImage(cvPixelBuffer: pixelBuffer)
                guard let cgImage = context.createCGImage(
                    image,
                    from: image.extent
                ) else { throw HelperError.imageWriteFailed }
                try writePNG(cgImage, to: outputURL)
                finish(0)
            } catch {
                fail(error.localizedDescription)
            }

        case .faceAlign:
            guard Date().timeIntervalSince(lastFaceCheck) >= 2 else { return }
            lastFaceCheck = Date()
            let request = VNDetectFaceRectanglesRequest()
            do {
                try VNImageRequestHandler(
                    cvPixelBuffer: pixelBuffer,
                    options: [:]
                ).perform([request])
                guard let face = request.results?.max(by: {
                    $0.boundingBox.width * $0.boundingBox.height
                        < $1.boundingBox.width * $1.boundingBox.height
                }) else {
                    emit("no-face")
                    return
                }
                let x = Int(round(face.boundingBox.midX * 100))
                // Vision coordinates begin at the lower-left; ScanBox reports
                // Y from the top to match its Windows guidance.
                let y = Int(round((1 - face.boundingBox.midY) * 100))
                emit("face\t\(x)\t\(y)")
            } catch {
                emit("no-face")
            }
        }
    }

    private func emit(_ line: String) {
        FileHandle.standardOutput.write(Data((line + "\n").utf8))
        fflush(stdout)
    }

    private func fail(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        finish(1)
    }

    private func finish(_ status: Int32) {
        guard !finished else { return }
        finished = true
        session.stopRunning()
        fflush(stdout)
        fflush(stderr)
        exit(status)
    }
}

private let scanBoxHotKeySignature: OSType = 0x53425848 // "SBXH"

private func scanBoxHotKeyHandler(
    _ nextHandler: EventHandlerCallRef?,
    _ event: EventRef?,
    _ userData: UnsafeMutableRawPointer?
) -> OSStatus {
    guard let event, let userData else { return OSStatus(eventNotHandledErr) }
    var hotKeyID = EventHotKeyID()
    let status = GetEventParameter(
        event,
        EventParamName(kEventParamDirectObject),
        EventParamType(typeEventHotKeyID),
        nil,
        MemoryLayout<EventHotKeyID>.size,
        nil,
        &hotKeyID
    )
    guard status == noErr, hotKeyID.signature == scanBoxHotKeySignature else {
        return OSStatus(eventNotHandledErr)
    }
    let service = Unmanaged<HotKeyService>.fromOpaque(userData)
        .takeUnretainedValue()
    service.handle(hotKeyID.id)
    return noErr
}

final class HotKeyService {
    private let outputDirectory: URL
    private var eventHandler: EventHandlerRef?
    private var hotKeys: [EventHotKeyRef?] = []
    private var captureInProgress = false

    init(outputDirectory: URL) {
        self.outputDirectory = outputDirectory
    }

    /// Check Accessibility trust without forcing a macOS prompt. ScanBox has
    /// its own Mac Permissions dialog for opening the right System Settings
    /// pane; prompting from this background helper on every launch produced
    /// repeated, hard-to-find windows for VoiceOver users.
    private func hasAccessibilityTrust() -> Bool {
        return AXIsProcessTrusted()
    }

    func start() throws {
        if !hasAccessibilityTrust() {
            emit(
                "accessibility",
                "ScanBox's global keyboard shortcuts may need Accessibility "
                    + "permission on this Mac. Open ScanBox's Mac Permissions "
                    + "guide, turn Accessibility on for ScanBox, then quit "
                    + "and reopen ScanBox."
            )
        }
        var eventType = EventTypeSpec(
            eventClass: OSType(kEventClassKeyboard),
            eventKind: UInt32(kEventHotKeyPressed)
        )
        let handlerStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            scanBoxHotKeyHandler,
            1,
            &eventType,
            Unmanaged.passUnretained(self).toOpaque(),
            &eventHandler
        )
        guard handlerStatus == noErr else {
            throw HelperError.hotKeyRegistrationFailed(handlerStatus)
        }

        try register(id: 1, keyCode: UInt32(kVK_ANSI_Backslash), modifiers: UInt32(controlKey))
        try register(id: 2, keyCode: UInt32(kVK_ANSI_Backslash), modifiers: UInt32(controlKey | shiftKey))
        try register(id: 3, keyCode: UInt32(kVK_ANSI_Backslash), modifiers: UInt32(controlKey | optionKey))
        try register(id: 4, keyCode: UInt32(kVK_ANSI_Slash), modifiers: UInt32(controlKey | shiftKey))
        try register(id: 5, keyCode: UInt32(kVK_ANSI_Minus), modifiers: UInt32(controlKey | shiftKey))
        emit("ready", "registered")
        startCommandReader()
    }

    private func register(id: UInt32, keyCode: UInt32, modifiers: UInt32) throws {
        var reference: EventHotKeyRef?
        var hotKeyID = EventHotKeyID(
            signature: scanBoxHotKeySignature,
            id: id
        )
        let status = RegisterEventHotKey(
            keyCode,
            modifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &reference
        )
        guard status == noErr else {
            throw HelperError.hotKeyRegistrationFailed(status)
        }
        hotKeys.append(reference)
    }

    private func startCommandReader() {
        DispatchQueue.global(qos: .utility).async {
            while let line = readLine() {
                let fields = line.split(separator: "\t", maxSplits: 1)
                guard fields.count == 2,
                      fields[0] == "announce",
                      let data = Data(base64Encoded: String(fields[1])),
                      let text = String(data: data, encoding: .utf8)
                else {
                    continue
                }
                DispatchQueue.main.async {
                    NSAccessibility.post(
                        element: NSApplication.shared,
                        notification: .announcementRequested,
                        userInfo: [
                            .announcement: text,
                            .priority: NSAccessibilityPriorityLevel.high.rawValue,
                        ]
                    )
                }
            }
        }
    }

    func handle(_ id: UInt32) {
        emit("hotkey", "\(id)")
        if id == 3 {
            emit("toggle", "")
            return
        }
        if id == 5 {
            emit("browser-pdf", "")
            return
        }
        guard !captureInProgress else { return }
        guard let application = NSWorkspace.shared.frontmostApplication else {
            emit("error", HelperError.noFrontmostApplication.localizedDescription)
            return
        }

        captureInProgress = true
        let pid = application.processIdentifier
        let kind = id == 2 ? "ocr" : (id == 4 ? "ask" : "describe")
        let outputURL = outputDirectory
            .appendingPathComponent("mac_active_\(UUID().uuidString).png")
        guard let windowID = ScanBoxMacHelper.foremostNormalWindowID(for: pid) else {
            emit("error", HelperError.noActiveWindow.localizedDescription)
            captureInProgress = false
            return
        }
        if kind == "ask" {
            emit(
                kind,
                "window\t\(windowID)\t\(outputURL.path)\t\(application.bundleIdentifier ?? "")"
            )
        } else {
            emit(kind, "window\t\(windowID)\t\(outputURL.path)")
        }
        captureInProgress = false
    }

    private func emit(_ kind: String, _ value: String) {
        let safeValue = value.replacingOccurrences(of: "\n", with: " ")
        FileHandle.standardOutput.write(Data("\(kind)\t\(safeValue)\n".utf8))
        fflush(stdout)
    }
}

final class ScannerService: NSObject, ICDeviceBrowserDelegate, ICScannerDeviceDelegate {
    enum Operation {
        case list
        case scan(identifier: String, outputDirectory: URL)
    }

    private let operation: Operation
    private let browser = ICDeviceBrowser()
    private var selectedScanner: ICScannerDevice?
    private var scannedURL: URL?
    private var finished = false
    private var sessionRequested = false
    private var selectingFlatbed = false
    private var flatbedSelectAttempts = 0

    init(operation: Operation) {
        self.operation = operation
        super.init()
        browser.delegate = self
        browser.browsedDeviceTypeMask = ICDeviceTypeMask(
            rawValue: ICDeviceTypeMask.scanner.rawValue
                | ICDeviceLocationTypeMask.local.rawValue
                | ICDeviceLocationTypeMask.remote.rawValue
        )!
    }

    func start() {
        browser.start()
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
            guard !self.finished else { return }
            switch self.operation {
            case .list:
                self.emitAvailableScanners()
                self.finish(0)
            case .scan:
                self.fail("The selected scanner is no longer available.")
            }
        }
    }

    func deviceBrowser(
        _ browser: ICDeviceBrowser,
        didAdd device: ICDevice,
        moreComing: Bool
    ) {
        guard let scanner = device as? ICScannerDevice else { return }
        switch operation {
        case .scan(let identifier, _):
            if scanner.uuidString == identifier {
                selectedScanner = scanner
                scanner.delegate = self
                // Shared network scanners need a brief moment after discovery
                // before ImageCaptureCore accepts an open-session request.
                DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
                    self.beginScanIfReady()
                }
            }
        case .list:
            // A scanner has appeared. Rather than always waiting out the
            // full 30 second discovery window (the previous behaviour,
            // which made every document/photo scan press pause for
            // 30 seconds even though the scanner usually answers in well
            // under a second), give any other scanners a brief moment to
            // appear alongside it and then finish. Repeated calls here are
            // harmless: emitAvailableScanners() re-reads the live device
            // list at whichever call fires first, so later arrivals up to
            // that point are still included, and `finished` prevents a
            // second finish() from a later scheduled call.
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                guard !self.finished else { return }
                self.emitAvailableScanners()
                self.finish(0)
            }
        }
    }

    func deviceBrowser(
        _ browser: ICDeviceBrowser,
        didRemove device: ICDevice,
        moreGoing: Bool
    ) {}

    func deviceBrowserDidEnumerateLocalDevices(_ browser: ICDeviceBrowser) {
        // Network scanners arrive after local enumeration. Keep browsing
        // until the short discovery window expires, or begin immediately if
        // the requested scanner has already appeared.
        beginScanIfReady()
    }

    private func beginScanIfReady() {
        guard case .scan = operation,
              let scanner = selectedScanner,
              !sessionRequested else { return }
        sessionRequested = true
        scanner.delegate = self
        scanner.requestOpenSession()
    }

    func device(_ device: ICDevice, didOpenSessionWithError error: Error?) {
        if let error {
            fail("The scanner could not be opened: \(error.localizedDescription)")
            return
        }
        guard let scanner = device as? ICScannerDevice else {
            fail("The selected device is not a scanner.")
            return
        }
        selectFlatbedWhenReady(scanner)
    }

    func device(_ device: ICDevice, didCloseSessionWithError error: Error?) {}
    func didRemove(_ device: ICDevice) {
        if device === selectedScanner && !finished {
            fail("The scanner was disconnected before the scan completed.")
        }
    }

    func scannerDevice(
        _ scanner: ICScannerDevice,
        didSelect functionalUnit: ICScannerFunctionalUnit,
        error: Error?
    ) {
        if let error {
            selectingFlatbed = false
            if flatbedSelectAttempts < 10 {
                emitError(
                    "flatbed-select retry=\(flatbedSelectAttempts) error=\(error.localizedDescription)"
                )
                DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                    self.selectFlatbedWhenReady(scanner)
                }
                return
            }
            fail("The flatbed could not be selected: \(error.localizedDescription)")
            return
        }
        beginScan(scanner, functionalUnit)
    }

    private func beginScan(
        _ scanner: ICScannerDevice,
        _ functionalUnit: ICScannerFunctionalUnit
    ) {
        guard case .scan(_, let outputDirectory) = operation else { return }
        scanner.transferMode = .fileBased
        scanner.downloadsDirectory = outputDirectory
        scanner.documentName = "scan_\(UUID().uuidString)"
        scanner.documentUTI = UTType.tiff.identifier
        functionalUnit.scanArea = NSRect(origin: .zero, size: functionalUnit.physicalSize)
        if functionalUnit.supportedResolutions.contains(300) {
            functionalUnit.resolution = 300
        }
        scanner.requestScan()
    }

    func scannerDevice(_ scanner: ICScannerDevice, didScanTo url: URL) {
        scannedURL = url
    }

    func scannerDevice(
        _ scanner: ICScannerDevice,
        didScanTo bandData: ICScannerBandData
    ) {}

    func scannerDevice(
        _ scanner: ICScannerDevice,
        didCompleteScanWithError error: Error?
    ) {
        if let error {
            fail("The scan failed: \(error.localizedDescription)")
            return
        }
        guard let scannedURL else {
            fail("The scanner completed without returning an image.")
            return
        }
        emit("scan", scannedURL.path)
        scanner.requestCloseSession()
        finish(0)
    }

    func scannerDevice(
        _ scanner: ICScannerDevice,
        didCompleteOverviewScanWithError error: Error?
    ) {}

    func scannerDeviceDidBecomeAvailable(_ scanner: ICScannerDevice) {
        guard scanner === selectedScanner else { return }
        selectFlatbedWhenReady(scanner)
    }

    private func selectFlatbedWhenReady(_ scanner: ICScannerDevice, attempt: Int = 0) {
        guard !finished, !selectingFlatbed else { return }
        // Some driverless AirScan/eSCL bridged devices (no vendor ICA driver,
        // scanning only through Apple's built-in eSCL support - as is now the
        // case for several current Brother models) already come up with the
        // flatbed as their current functional unit, and reject an explicit
        // re-selection of the unit they are already on with a generic
        // com.apple.ImageCaptureCore -9922 error. Apple's own Image Capture
        // app never re-selects a unit that is already active, so mirror that
        // here instead of unconditionally calling requestSelect.
        let current = scanner.selectedFunctionalUnit
        if current.type == .flatbed {
            emitError("scan-source-check attempt=\(attempt) already-flatbed")
            beginScan(scanner, current)
            return
        }
        let types = scanner.availableFunctionalUnitTypes.map { $0.intValue }
        let hasFlatbed = scanner.availableFunctionalUnitTypes.contains(
            NSNumber(value: ICScannerFunctionalUnitType.flatbed.rawValue)
        )
        emitError("scan-source-check attempt=\(attempt) types=\(types)")
        if hasFlatbed {
            selectingFlatbed = true
            flatbedSelectAttempts += 1
            scanner.requestSelect(.flatbed)
            return
        }
        if types.isEmpty && attempt < 15 {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1) {
                self.selectFlatbedWhenReady(scanner, attempt: attempt + 1)
            }
            return
        }
        fail("The selected scanner did not report a flatbed source to macOS.")
    }

    private func emitAvailableScanners() {
        let scanners = (browser.devices ?? []).compactMap {
            $0 as? ICScannerDevice
        }
        for scanner in scanners {
            let identifier = scanner.uuidString ?? scanner.persistentIDString ?? ""
            guard !identifier.isEmpty else { continue }
            emit("scanner", identifier, scanner.name ?? "Unnamed scanner")
        }
    }

    private func emit(_ fields: String...) {
        let safe = fields.map {
            $0.replacingOccurrences(of: "\t", with: " ")
              .replacingOccurrences(of: "\n", with: " ")
        }
        FileHandle.standardOutput.write(Data((safe.joined(separator: "\t") + "\n").utf8))
    }

    private func fail(_ message: String) {
        emitError(message)
        selectedScanner?.requestCloseSession()
        finish(1)
    }

    private func emitError(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        fflush(stderr)
    }

    private func finish(_ status: Int32) {
        guard !finished else { return }
        finished = true
        browser.stop()
        fflush(stdout)
        fflush(stderr)
        exit(status)
    }
}

@main
struct ScanBoxMacHelper {
    static func main() {
        do {
            if CommandLine.arguments.count == 3,
               CommandLine.arguments[1] == "ocr" {
                let imageURL = URL(fileURLWithPath: CommandLine.arguments[2])
                guard let image = NSImage(contentsOf: imageURL),
                      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
                else { throw HelperError.imageWriteFailed }
                FileHandle.standardOutput.write(
                    Data(try recognisedText(in: cgImage).utf8)
                )
                return
            }
            if CommandLine.arguments.count == 3,
               CommandLine.arguments[1] == "orientation" {
                let imageURL = URL(fileURLWithPath: CommandLine.arguments[2])
                guard let image = NSImage(contentsOf: imageURL),
                      let cgImage = image.cgImage(forProposedRect: nil, context: nil, hints: nil)
                else { throw HelperError.imageWriteFailed }
                let rotation = try recognisedTextRotation(in: cgImage)
                let output = rotation.map(String.init) ?? "unknown"
                FileHandle.standardOutput.write(Data("\(output)\n".utf8))
                return
            }
            if CommandLine.arguments.count == 4,
               CommandLine.arguments[1] == "capture-camera",
               let index = Int(CommandLine.arguments[2]) {
                let service = CameraService(
                    deviceIndex: index,
                    operation: .still(URL(fileURLWithPath: CommandLine.arguments[3]))
                )
                try service.start()
                RunLoop.main.run()
                return
            }
            if CommandLine.arguments.count == 3,
               CommandLine.arguments[1] == "face-align",
               let index = Int(CommandLine.arguments[2]) {
                let service = CameraService(
                    deviceIndex: index,
                    operation: .faceAlign
                )
                try service.start()
                RunLoop.main.run()
                return
            }
            if CommandLine.arguments.count == 4,
               CommandLine.arguments[1] == "read-pdf" {
                let pdfURL = URL(fileURLWithPath: CommandLine.arguments[2])
                let useOCR = CommandLine.arguments[3] == "ocr"
                guard let document = PDFDocument(url: pdfURL),
                      document.pageCount > 0 else {
                    throw HelperError.imageWriteFailed
                }
                for index in 0..<document.pageCount {
                    guard let page = document.page(at: index) else { continue }
                    let selectableText = (page.string ?? "").trimmingCharacters(
                        in: .whitespacesAndNewlines
                    )
                    var text = selectableText
                    if useOCR {
                        let bounds = page.bounds(for: .mediaBox)
                        let target = NSSize(
                            width: max(1, bounds.width * 2),
                            height: max(1, bounds.height * 2)
                        )
                        let image = page.thumbnail(of: target, for: .mediaBox)
                        if let cgImage = image.cgImage(
                            forProposedRect: nil,
                            context: nil,
                            hints: nil
                        ) {
                            let recognised = try recognisedText(in: cgImage)
                                .trimmingCharacters(in: .whitespacesAndNewlines)
                            if !recognised.isEmpty {
                                text = recognised
                            }
                        }
                    }
                    let encoded = Data(text.utf8).base64EncodedString()
                    FileHandle.standardOutput.write(
                        Data("page\t\(encoded)\n".utf8)
                    )
                }
                return
            }
            // "choose-files" (a native NSOpenPanel run from this helper as
            // a separate process) has been removed. It had two mutually
            // exclusive failure modes depending on activation policy -
            // VoiceOver only exposed a handful of file-list rows under
            // .accessory, and the picker intermittently stopped responding
            // under .regular, with no consistent trigger found. Import now
            // uses wx.FileDialog directly from scanbox.py instead, the same
            // in-process native picker Windows already uses.
            if CommandLine.arguments.count == 2,
               CommandLine.arguments[1] == "list-scanners" {
                let service = ScannerService(operation: .list)
                service.start()
                RunLoop.main.run()
                return
            }
            if CommandLine.arguments.count == 2,
               CommandLine.arguments[1] == "list-cameras" {
                for (index, device) in AVCaptureDevice.devices(for: .video).enumerated() {
                    let name = device.localizedName
                        .replacingOccurrences(of: "\t", with: " ")
                        .replacingOccurrences(of: "\n", with: " ")
                    FileHandle.standardOutput.write(
                        Data("camera\t\(index)\t\(name)\n".utf8)
                    )
                }
                return
            }
            if CommandLine.arguments.count == 2,
               CommandLine.arguments[1] == "request-screen-recording" {
                if CGPreflightScreenCaptureAccess() || CGRequestScreenCaptureAccess() {
                    FileHandle.standardOutput.write(Data("granted\n".utf8))
                } else {
                    FileHandle.standardOutput.write(Data("not-granted\n".utf8))
                }
                return
            }
            if CommandLine.arguments.count == 4,
               CommandLine.arguments[1] == "scan" {
                let service = ScannerService(
                    operation: .scan(
                        identifier: CommandLine.arguments[2],
                        outputDirectory: URL(
                            fileURLWithPath: CommandLine.arguments[3],
                            isDirectory: true
                        )
                    )
                )
                service.start()
                RunLoop.main.run()
                return
            }
            guard CommandLine.arguments.count == 3,
                  CommandLine.arguments[1] == "listen" else {
                throw HelperError.usage
            }
            let service = HotKeyService(
                outputDirectory: URL(
                    fileURLWithPath: CommandLine.arguments[2],
                    isDirectory: true
                )
            )
            // Accessory applications do not appear in the Dock, but unlike a
            // prohibited background process they can emit VoiceOver
            // accessibility announcements.
            NSApplication.shared.setActivationPolicy(.accessory)
            try service.start()
            NSApplication.shared.run()
        } catch {
            let message = error.localizedDescription
                .replacingOccurrences(of: "\n", with: " ")
            FileHandle.standardOutput.write(
                Data("fatal\t\(message)\n".utf8)
            )
            exit(1)
        }
    }

    static func captureWindow(for pid: pid_t, to outputURL: URL) async throws {
        if !CGPreflightScreenCaptureAccess() && !CGRequestScreenCaptureAccess() {
            throw HelperError.permissionDenied
        }
        guard let windowID = foremostNormalWindowID(for: pid) else {
            throw HelperError.noActiveWindow
        }

        let content = try await SCShareableContent.excludingDesktopWindows(
            true,
            onScreenWindowsOnly: true
        )
        guard let window = content.windows.first(where: {
            $0.windowID == windowID && $0.owningApplication?.processID == pid
        }) else {
            throw HelperError.noActiveWindow
        }

        let filter = SCContentFilter(desktopIndependentWindow: window)
        let configuration = SCStreamConfiguration()
        configuration.width = max(1, Int(window.frame.width * 2.0))
        configuration.height = max(1, Int(window.frame.height * 2.0))
        configuration.showsCursor = false

        let image = try await SCScreenshotManager.captureImage(
            contentFilter: filter,
            configuration: configuration
        )
        guard let destination = CGImageDestinationCreateWithURL(
            outputURL as CFURL,
            UTType.png.identifier as CFString,
            1,
            nil
        ) else {
            throw HelperError.imageWriteFailed
        }
        CGImageDestinationAddImage(destination, image, nil)
        guard CGImageDestinationFinalize(destination) else {
            throw HelperError.imageWriteFailed
        }
    }

    static func foremostNormalWindowID(for pid: pid_t) -> CGWindowID? {
        let options: CGWindowListOption = [
            .optionOnScreenOnly,
            .excludeDesktopElements,
        ]
        guard let windows = CGWindowListCopyWindowInfo(options, kCGNullWindowID)
            as? [[String: Any]] else {
            return nil
        }
        for window in windows {
            guard let owner = window[kCGWindowOwnerPID as String] as? NSNumber,
                  owner.int32Value == pid,
                  let layer = window[kCGWindowLayer as String] as? NSNumber,
                  layer.intValue == 0,
                  ((window[kCGWindowAlpha as String] as? NSNumber)?.doubleValue
                    ?? 1.0) > 0,
                  let number = window[kCGWindowNumber as String] as? NSNumber
            else {
                continue
            }
            return CGWindowID(number.uint32Value)
        }
        return nil
    }
}
