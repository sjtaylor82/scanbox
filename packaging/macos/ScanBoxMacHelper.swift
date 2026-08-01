import AppKit
import AVFoundation
import Carbon.HIToolbox
import CoreGraphics
import Darwin
import Foundation
import ImageCaptureCore
import ImageIO
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

    var errorDescription: String? {
        switch self {
        case .usage:
            return "Usage: scanbox-macos-helper listen OUTPUT_DIRECTORY | "
                + "list-scanners | list-cameras | "
                + "scan SCANNER_ID OUTPUT_DIRECTORY | ocr IMAGE_PATH"
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
        }
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

    func start() throws {
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
                            .priority: NSAccessibilityPriorityLevel.medium.rawValue,
                        ]
                    )
                }
            }
        }
    }

    func handle(_ id: UInt32) {
        if id == 3 {
            emit("toggle", "")
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
        Task {
            do {
                try await ScanBoxMacHelper.captureWindow(for: pid, to: outputURL)
                if kind == "ask" {
                    emit(kind, outputURL.path + "\t" + (application.bundleIdentifier ?? ""))
                } else {
                    emit(kind, outputURL.path)
                }
            } catch {
                emit("error", error.localizedDescription)
            }
            captureInProgress = false
        }
    }

    private func emit(_ kind: String, _ value: String) {
        let safeValue = value.replacingOccurrences(of: "\n", with: " ")
        FileHandle.standardOutput.write(Data("\(kind)\t\(safeValue)\n".utf8))
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

    init(operation: Operation) {
        self.operation = operation
        super.init()
        browser.delegate = self
        browser.browsedDeviceTypeMask = .scanner
    }

    func start() {
        browser.start()
    }

    func deviceBrowser(
        _ browser: ICDeviceBrowser,
        didAdd device: ICDevice,
        moreComing: Bool
    ) {
        guard let scanner = device as? ICScannerDevice else { return }
        if case .scan(let identifier, _) = operation,
           scanner.uuidString == identifier {
            selectedScanner = scanner
        }
    }

    func deviceBrowser(
        _ browser: ICDeviceBrowser,
        didRemove device: ICDevice,
        moreGoing: Bool
    ) {}

    func deviceBrowserDidEnumerateLocalDevices(_ browser: ICDeviceBrowser) {
        switch operation {
        case .list:
            let scanners = (browser.devices ?? []).compactMap {
                $0 as? ICScannerDevice
            }
            for scanner in scanners {
                let identifier = scanner.uuidString ?? scanner.persistentIDString ?? ""
                guard !identifier.isEmpty else { continue }
                emit("scanner", identifier, scanner.name ?? "Unnamed scanner")
            }
            finish(0)
        case .scan:
            guard let scanner = selectedScanner else {
                fail("The selected scanner is no longer available.")
                return
            }
            scanner.delegate = self
            scanner.requestOpenSession()
        }
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
        guard scanner.availableFunctionalUnitTypes.contains(
            NSNumber(value: ICScannerFunctionalUnitType.flatbed.rawValue)
        ) else {
            fail("The selected scanner does not provide a flatbed source.")
            return
        }
        scanner.requestSelect(.flatbed)
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
            fail("The flatbed could not be selected: \(error.localizedDescription)")
            return
        }
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

    func scannerDeviceDidBecomeAvailable(_ scanner: ICScannerDevice) {}

    private func emit(_ fields: String...) {
        let safe = fields.map {
            $0.replacingOccurrences(of: "\t", with: " ")
              .replacingOccurrences(of: "\n", with: " ")
        }
        FileHandle.standardOutput.write(Data((safe.joined(separator: "\t") + "\n").utf8))
    }

    private func fail(_ message: String) {
        FileHandle.standardError.write(Data((message + "\n").utf8))
        selectedScanner?.requestCloseSession()
        finish(1)
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
                let request = VNRecognizeTextRequest()
                request.recognitionLevel = .accurate
                request.usesLanguageCorrection = true
                let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
                try handler.perform([request])
                let observations = (request.results ?? []).sorted { left, right in
                    if abs(left.boundingBox.midY - right.boundingBox.midY) > 0.015 {
                        return left.boundingBox.midY > right.boundingBox.midY
                    }
                    return left.boundingBox.minX < right.boundingBox.minX
                }
                let lines = observations.compactMap { $0.topCandidates(1).first?.string }
                FileHandle.standardOutput.write(Data(lines.joined(separator: "\n").utf8))
                return
            }
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
            NSApplication.shared.setActivationPolicy(.prohibited)
            try service.start()
            RunLoop.main.run()
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
