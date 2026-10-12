"""macOS CoreLocation 기반 기기 실제 위치 조회.

IP 지오로케이션은 ISP 백본 POP(예: 용인 → 마포구)을 반환해 부정확하다.
CoreLocation은 WiFi/블루투스/GPS 기반이라 시/구/동 단위로 정확하다 — 판매용 앱에 적합.

전제: macOS 위치 서비스 ON + 호스트 앱(Terminal/번들 .app)에 위치 권한 허용.
권한 거부/미지원 시 None 반환 → 호출부가 IP 폴백.
별도 pip 의존성 없이 objc.loadBundle로 CoreLocation.framework를 동적 로드한다.
"""
import time

try:
    import objc
    from Foundation import NSObject, NSRunLoop, NSDate, NSDefaultRunLoopMode
    _OBJC_OK = True
except Exception:
    _OBJC_OK = False

_CL_LOADED = False
_CLLocationManager = None


def _load_corelocation():
    global _CL_LOADED, _CLLocationManager
    if _CL_LOADED:
        return _CLLocationManager is not None
    _CL_LOADED = True
    if not _OBJC_OK:
        return False
    try:
        ns = {}
        objc.loadBundle("CoreLocation", ns,
                        "/System/Library/Frameworks/CoreLocation.framework")
        _CLLocationManager = ns.get("CLLocationManager")
        return _CLLocationManager is not None
    except Exception:
        _CLLocationManager = None
        return False


if _OBJC_OK:
    class _LocDelegate(NSObject):
        def init(self):
            self = objc.super(_LocDelegate, self).init()
            if self is None:
                return None
            self.coord = None
            self.failed = False
            return self

        def locationManager_didUpdateLocations_(self, manager, locations):
            try:
                loc = locations.lastObject()
                c = loc.coordinate()
                self.coord = (float(c.latitude), float(c.longitude),
                              float(loc.horizontalAccuracy()))
            except Exception:
                self.failed = True

        def locationManager_didFailWithError_(self, manager, error):
            self.failed = True


def get_device_coordinates(timeout=8.0):
    """기기 실제 위치 (lat, lon, accuracy_m) 반환. 실패/권한없음 시 None.
    메인 스레드에서 호출하는 것이 가장 안정적(CoreLocation은 run loop 기반)."""
    if not _load_corelocation():
        return None
    try:
        if not _CLLocationManager.locationServicesEnabled():
            return None
        mgr = _CLLocationManager.alloc().init()
        # authorizationStatus: 0=notDetermined 1=restricted 2=denied 3=always 4=whenInUse
        try:
            st = mgr.authorizationStatus()
            if st in (1, 2):   # 거부/제한 → 권한 팝업 못 띄움, 즉시 폴백
                return None
        except Exception:
            pass
        delg = _LocDelegate.alloc().init()
        mgr.setDelegate_(delg)
        try:
            mgr.setDesiredAccuracy_(100.0)  # ~100m, 동/구 식별엔 충분
        except Exception:
            pass
        try:
            mgr.requestWhenInUseAuthorization()
        except Exception:
            pass
        mgr.startUpdatingLocation()
        rl = NSRunLoop.currentRunLoop()
        deadline = time.time() + timeout
        while delg.coord is None and not delg.failed and time.time() < deadline:
            rl.runMode_beforeDate_(NSDefaultRunLoopMode,
                                   NSDate.dateWithTimeIntervalSinceNow_(0.2))
        try:
            mgr.stopUpdatingLocation()
        except Exception:
            pass
        return delg.coord
    except Exception:
        return None


if __name__ == "__main__":
    print("CoreLocation loaded:", _load_corelocation())
    print("coords:", get_device_coordinates())
