import CoreLocation
import Foundation

let sem = DispatchSemaphore(value: 0)
var exitCode = 0

class LocDelegate: NSObject, CLLocationManagerDelegate {
    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        switch manager.authorizationStatus {
        case .authorizedAlways, .authorizedWhenInUse:
            sem.signal()
        case .denied, .restricted:
            exitCode = 1
            sem.signal()
        case .notDetermined:
            break
        @unknown default:
            exitCode = 1
            sem.signal()
        }
    }
}

guard CLLocationManager.locationServicesEnabled() else {
    exit(3)
}

let delegate = LocDelegate()
let manager = CLLocationManager()
manager.delegate = delegate

switch manager.authorizationStatus {
case .authorizedAlways, .authorizedWhenInUse:
    break
case .notDetermined:
    manager.requestWhenInUseAuthorization()
    sem.wait()
case .denied, .restricted:
    exitCode = 1
@unknown default:
    exitCode = 1
}

exit(Int32(exitCode))
