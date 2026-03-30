// NWBrowser-based local network permission probe for macOS Sequoia+.
//
// NWBrowser is the ONLY Apple API that reliably reports permission state:
//   - nw_browser_state_ready   => permission granted, browsing active
//   - nw_browser_state_waiting => with error: permission denied (PolicyDenied)
//   - nw_browser_state_failed  => permanent failure (likely denied)
//   - timeout                  => dialog is still visible / user hasn't responded
//
// Raw UDP sockets and even DNSServiceBrowse cannot distinguish "granted"
// from "dialog is currently showing" - they both return success.

#import <Network/Network.h>
#include <dispatch/dispatch.h>

// Probe local network permission using NWBrowser.
//
// Returns:
//   0 = timeout (dialog may be showing, user hasn't responded yet)
//   1 = granted (NWBrowser reached .ready state)
//   2 = denied  (NWBrowser reported PolicyDenied or failed)
int nw_probe_local_network(double timeout_secs) {
    __block int result = 0;
    dispatch_semaphore_t semaphore = dispatch_semaphore_create(0);

    nw_browse_descriptor_t descriptor =
        nw_browse_descriptor_create_bonjour_service("_reachy-mini._tcp", "local.");

    nw_browser_t browser = nw_browser_create(descriptor, NULL);

    nw_browser_set_state_changed_handler(browser,
        ^(nw_browser_state_t state, nw_error_t error) {
            switch (state) {
                case nw_browser_state_ready:
                    result = 1;
                    dispatch_semaphore_signal(semaphore);
                    break;

                case nw_browser_state_waiting:
                    if (error) {
                        result = 2;
                        dispatch_semaphore_signal(semaphore);
                    }
                    break;

                case nw_browser_state_failed:
                    result = 2;
                    dispatch_semaphore_signal(semaphore);
                    break;

                default:
                    break;
            }
        });

    nw_browser_set_queue(browser,
        dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0));
    nw_browser_start(browser);

    dispatch_time_t deadline = dispatch_time(
        DISPATCH_TIME_NOW,
        (int64_t)(timeout_secs * NSEC_PER_SEC));
    dispatch_semaphore_wait(semaphore, deadline);

    nw_browser_cancel(browser);

    return result;
}
