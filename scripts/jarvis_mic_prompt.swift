import AVFoundation
import Foundation

let sem = DispatchSemaphore(value: 0)
var exitCode = 0

switch AVCaptureDevice.authorizationStatus(for: .audio) {
case .authorized:
    break
case .notDetermined:
    AVCaptureDevice.requestAccess(for: .audio) { granted in
        if !granted { exitCode = 2 }
        sem.signal()
    }
    sem.wait()
case .denied, .restricted:
    exitCode = 1
@unknown default:
    exitCode = 1
}

exit(Int32(exitCode))
