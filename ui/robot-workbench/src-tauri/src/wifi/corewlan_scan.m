// CoreWLAN WiFi scanner + Location permission helper for macOS.
//
// Location Services must be granted for SSID/BSSID to be visible in CoreWLAN.
// Without it, CWNetwork returns nil for ssid, ssidData, and bssid.
//
// All CLLocationManager access is serialized on the main queue to avoid
// thread-safety issues (the manager and delegate are stored in statics).

#import <CoreWLAN/CoreWLAN.h>
#import <CoreLocation/CoreLocation.h>
#import <Foundation/Foundation.h>

// ---------------------------------------------------------------------------
// Location permission
// ---------------------------------------------------------------------------

// Cached authorization status, updated by the delegate.
// -1 = not yet initialized.  0-4 = CLAuthorizationStatus value.
// Declared before the delegate @implementation so the callback can write it.
static volatile int _locationAuthStatus = -1;

@interface _WifiLocationDelegate : NSObject <CLLocationManagerDelegate>
@end

@implementation _WifiLocationDelegate
- (void)locationManagerDidChangeAuthorization:(CLLocationManager *)manager {
    int status = (int)[manager authorizationStatus];
    _locationAuthStatus = status;
    NSLog(@"[wifi] Location authorization changed: %d (cache updated)", status);
}
@end

// Persistent manager + delegate (must outlive the request).
// Created once on the main queue via ensure_manager().
static CLLocationManager *_locationManager = nil;
static _WifiLocationDelegate *_locationDelegate = nil;

/// Ensure the CLLocationManager exists. Must be called on the main queue.
static void ensure_manager(void) {
    if (!_locationManager) {
        _locationManager = [[CLLocationManager alloc] init];
        _locationDelegate = [[_WifiLocationDelegate alloc] init];
        _locationManager.delegate = _locationDelegate;
        // Seed the cache from the real system status immediately.
        _locationAuthStatus = (int)[_locationManager authorizationStatus];
        NSLog(@"[wifi] CLLocationManager created, initial status: %d", _locationAuthStatus);
    }
}

/// Check current location authorization status.
///
/// On the first call (cold start), creates the CLLocationManager on the main
/// queue and reads its instance authorizationStatus property. The deprecated
/// class method +[CLLocationManager authorizationStatus] is NOT used because
/// it can incorrectly return notDetermined(0) on macOS 11+ even when the user
/// already granted permission in a previous session. The instance property is
/// the only reliable source of truth.
///
/// Subsequent calls return the cached value updated by the delegate.
int corewlan_location_status(void) {
    // Fast path: delegate already populated the cache (or ensure_manager ran).
    if (_locationAuthStatus >= 0) {
        return _locationAuthStatus;
    }

    // Cold-start path: create the manager on the main queue so we read the
    // instance authorizationStatus (accurate) rather than the class method
    // (deprecated, unreliable at cold start on macOS 11+).
    if ([NSThread isMainThread]) {
        ensure_manager();
    } else {
        dispatch_sync(dispatch_get_main_queue(), ^{
            ensure_manager();
        });
    }

    return _locationAuthStatus;
}

/// Request location permission from any non-main thread.
///
/// Uses dispatch_sync (no timeout) so the call NEVER silently fails when
/// the main queue is momentarily busy (e.g. Vite HMR reloads in dev mode).
/// Safe to call from Tokio spawn_blocking threads.
///
/// Returns the authorization status *before* the user responds
/// (dialog may be pending). The delegate will update _locationAuthStatus
/// once the user makes a choice.
int corewlan_request_location(void) {
    // Guard: calling dispatch_sync from the main thread would deadlock.
    if ([NSThread isMainThread]) {
        ensure_manager();
        int status = _locationAuthStatus;
        if (status == 0 /* kCLAuthorizationStatusNotDetermined */) {
            NSLog(@"[wifi] Requesting location permission (main thread)...");
            [_locationManager requestWhenInUseAuthorization];
        }
        return status;
    }

    __block int status = 0;
    dispatch_sync(dispatch_get_main_queue(), ^{
        ensure_manager();
        status = _locationAuthStatus;
        if (status == 0 /* kCLAuthorizationStatusNotDetermined */) {
            NSLog(@"[wifi] Requesting location permission (dispatch_sync to main queue)...");
            [_locationManager requestWhenInUseAuthorization];
        }
    });
    return status;
}

// ---------------------------------------------------------------------------
// WiFi scan
// ---------------------------------------------------------------------------

static __thread char *_scan_buf = NULL;

/// Scan nearby WiFi networks via CoreWLAN.
///
/// Returns a JSON C-string: `[{"ssid":"...","rssi":-50}, ...]`
/// Returns NULL on error. Caller must NOT free the result.
const char *corewlan_scan_networks(void) {
    @autoreleasepool {
        CWWiFiClient *client = [CWWiFiClient sharedWiFiClient];
        if (!client) return NULL;

        CWInterface *iface = [client interface];
        if (!iface) return NULL;

        NSError *error = nil;
        NSSet<CWNetwork *> *networks = [iface scanForNetworksWithName:nil
                                                                error:&error];
        if (error) {
            NSLog(@"[wifi] CoreWLAN scan error: %@", error.localizedDescription);
            return NULL;
        }
        if (!networks) return NULL;

        NSMutableArray *results = [NSMutableArray arrayWithCapacity:networks.count];
        NSMutableSet *seen = [NSMutableSet set];

        for (CWNetwork *net in networks) {
            NSString *ssid = net.ssid;
            if (!ssid && net.ssidData) {
                ssid = [[NSString alloc] initWithData:net.ssidData
                                             encoding:NSUTF8StringEncoding];
            }
            if (!ssid || ssid.length == 0 || [seen containsObject:ssid]) continue;
            [seen addObject:ssid];

            [results addObject:@{
                @"ssid": ssid,
                @"rssi": @(net.rssiValue)
            }];
        }

        NSData *json = [NSJSONSerialization dataWithJSONObject:results
                                                       options:0
                                                         error:nil];
        if (!json) return NULL;

        NSString *jsonStr = [[NSString alloc] initWithData:json
                                                  encoding:NSUTF8StringEncoding];
        free(_scan_buf);
        _scan_buf = strdup([jsonStr UTF8String]);
        return _scan_buf;
    }
}
