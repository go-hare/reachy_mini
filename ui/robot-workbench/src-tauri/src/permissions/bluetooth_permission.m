/**
 * Bluetooth permission helper for macOS.
 *
 * Uses CoreBluetooth CBManager.authorization (macOS 10.15+) to check and
 * request Bluetooth permission without creating a long-lived manager.
 *
 * CBManagerAuthorization values:
 *   0 = notDetermined
 *   1 = restricted
 *   2 = denied
 *   3 = allowedAlways
 */

#import <CoreBluetooth/CoreBluetooth.h>
#import <Foundation/Foundation.h>

// Persistent CBCentralManager required to trigger and receive the dialog.
static CBCentralManager *_btManager = nil;
static volatile int _btAuthStatus = -1;

@interface _BTPermissionDelegate : NSObject <CBCentralManagerDelegate>
@end

@implementation _BTPermissionDelegate

- (void)centralManagerDidUpdateState:(CBCentralManager *)central {
    _btAuthStatus = (int)central.authorization;
    NSLog(@"[bt] centralManagerDidUpdateState: authorization=%d", _btAuthStatus);
}

@end

static _BTPermissionDelegate *_btDelegate = nil;

/**
 * Returns the current Bluetooth authorization status.
 * Uses the CBManager class-level property which is thread-safe.
 * Returns: 0=notDetermined, 1=restricted, 2=denied, 3=allowedAlways
 */
int bluetooth_authorization_status(void) {
    // CBManager.authorization is a class property - safe to call from any thread.
    int status = (int)[CBManager authorization];
    if (status != 0 /* kCBManagerAuthorizationStatusNotDetermined */) {
        _btAuthStatus = status;
    } else if (_btAuthStatus >= 0) {
        // Use cached value if we already received a delegate callback
        return _btAuthStatus;
    }
    return status;
}

/**
 * Triggers the system Bluetooth permission dialog by instantiating a
 * CBCentralManager on the main queue. Keeps the manager alive so the
 * delegate can receive authorization-change callbacks.
 *
 * Must be called from a background thread; blocks until the main-queue
 * block executes.
 */
void bluetooth_request_permission(void) {
    if ([NSThread isMainThread]) {
        if (!_btManager) {
            _btDelegate = [[_BTPermissionDelegate alloc] init];
            _btManager = [[CBCentralManager alloc] initWithDelegate:_btDelegate
                                                              queue:dispatch_get_main_queue()
                                                            options:@{CBCentralManagerOptionShowPowerAlertKey: @NO}];
        }
        return;
    }
    dispatch_sync(dispatch_get_main_queue(), ^{
        if (!_btManager) {
            _btDelegate = [[_BTPermissionDelegate alloc] init];
            _btManager = [[CBCentralManager alloc] initWithDelegate:_btDelegate
                                                              queue:dispatch_get_main_queue()
                                                            options:@{CBCentralManagerOptionShowPowerAlertKey: @NO}];
        }
    });
}
