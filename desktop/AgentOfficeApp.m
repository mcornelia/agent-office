#import <Cocoa/Cocoa.h>
#import <WebKit/WebKit.h>

typedef void (^ControllerCompletion)(BOOL success, NSString *output);

@interface AgentOfficeApp : NSObject <NSApplicationDelegate, NSWindowDelegate, WKNavigationDelegate>
@property(nonatomic, strong) NSStatusItem *statusItem;
@property(nonatomic, strong) NSWindow *window;
@property(nonatomic, strong) WKWebView *webView;
@property(nonatomic, strong) NSMenuItem *serverItem;
@property(nonatomic, strong) NSMenuItem *loginItem;
@property(nonatomic) BOOL revealWhenLoaded;
@end

@implementation AgentOfficeApp

- (NSURL *)runtimeURL {
    return [[NSBundle mainBundle].resourceURL URLByAppendingPathComponent:@"runtime" isDirectory:YES];
}

- (NSURL *)controllerURL {
    return [[self runtimeURL] URLByAppendingPathComponent:@"desktop/agent_office_ctl.py"];
}

- (void)applicationDidFinishLaunching:(NSNotification *)notification {
    (void)notification;
    [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];
    [self configureMenu];
    [self refreshLoginStatus];
    [self refreshServerStatus];
    if ([[[NSProcessInfo processInfo] arguments] containsObject:@"--login"]) {
        [self ensureServerShowingError:NO completion:^(BOOL success) { (void)success; }];
    } else {
        [self showOffice:nil];
    }
}

- (NSMenuItem *)menuItem:(NSString *)title action:(SEL)action key:(NSString *)key {
    NSMenuItem *item = [[NSMenuItem alloc] initWithTitle:title action:action keyEquivalent:key];
    item.target = self;
    return item;
}

- (void)configureMenu {
    self.statusItem = [[NSStatusBar systemStatusBar] statusItemWithLength:NSSquareStatusItemLength];
    NSImage *image = [NSImage imageWithSystemSymbolName:@"building.2" accessibilityDescription:@"Agent Office"];
    image.template = YES;
    self.statusItem.button.image = image;

    NSMenu *menu = [[NSMenu alloc] init];
    NSMenuItem *heading = [[NSMenuItem alloc] initWithTitle:@"Agent Office" action:nil keyEquivalent:@""];
    heading.enabled = NO;
    [menu addItem:heading];
    [menu addItem:[self menuItem:@"Open Office" action:@selector(showOffice:) key:@"o"]];
    [menu addItem:[self menuItem:@"Open Presentation" action:@selector(showPresentation:) key:@"p"]];
    [menu addItem:[NSMenuItem separatorItem]];
    self.serverItem = [[NSMenuItem alloc] initWithTitle:@"Server: checking…" action:nil keyEquivalent:@""];
    self.serverItem.enabled = NO;
    [menu addItem:self.serverItem];
    self.loginItem = [self menuItem:@"Start at Login" action:@selector(toggleLogin:) key:@""];
    [menu addItem:self.loginItem];
    [menu addItem:[self menuItem:@"Open Server Log" action:@selector(openLog:) key:@"l"]];
    [menu addItem:[self menuItem:@"Stop Local Server" action:@selector(stopServer:) key:@""]];
    [menu addItem:[NSMenuItem separatorItem]];
    [menu addItem:[self menuItem:@"Quit Agent Office" action:@selector(quit:) key:@"q"]];
    self.statusItem.menu = menu;
}

- (void)runController:(NSArray<NSString *> *)arguments completion:(ControllerCompletion)completion {
    NSString *controller = self.controllerURL.path;
    NSString *root = self.runtimeURL.path;
    dispatch_async(dispatch_get_global_queue(QOS_CLASS_USER_INITIATED, 0), ^{
        NSTask *task = [[NSTask alloc] init];
        NSPipe *pipe = [NSPipe pipe];
        task.executableURL = [NSURL fileURLWithPath:@"/usr/bin/python3"];
        task.arguments = [@ [controller, @"--root", root] arrayByAddingObjectsFromArray:arguments];
        task.standardOutput = pipe;
        task.standardError = pipe;
        BOOL launched = NO;
        NSString *failure = @"";
        @try {
            [task launch];
            launched = YES;
            [task waitUntilExit];
        } @catch (NSException *exception) {
            failure = exception.reason ?: @"Controller could not be launched";
        }
        NSData *data = [[pipe fileHandleForReading] readDataToEndOfFile];
        NSString *output = [[NSString alloc] initWithData:data encoding:NSUTF8StringEncoding] ?: @"";
        output = [output stringByTrimmingCharactersInSet:[NSCharacterSet whitespaceAndNewlineCharacterSet]];
        if (output.length == 0 && failure.length > 0) output = failure;
        BOOL success = launched && task.terminationStatus == 0;
        dispatch_async(dispatch_get_main_queue(), ^{ completion(success, output); });
    });
}

- (void)ensureServerShowingError:(BOOL)showError completion:(void (^)(BOOL success))completion {
    self.serverItem.title = @"Server: starting…";
    [self runController:@[@"start"] completion:^(BOOL success, NSString *output) {
        self.serverItem.title = success ? @"Server: running locally" : @"Server: unavailable";
        if (!success && showError) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Agent Office could not start";
            alert.informativeText = output.length ? output : @"Check the private server log for details.";
            alert.alertStyle = NSAlertStyleWarning;
            [alert runModal];
        }
        completion(success);
    }];
}

- (void)makeWindowIfNeeded {
    if (self.window) return;
    WKWebViewConfiguration *configuration = [[WKWebViewConfiguration alloc] init];
    configuration.websiteDataStore = [WKWebsiteDataStore nonPersistentDataStore];
    self.webView = [[WKWebView alloc] initWithFrame:NSZeroRect configuration:configuration];
    self.webView.navigationDelegate = self;
    NSWindowStyleMask style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable |
                              NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskResizable;
    self.window = [[NSWindow alloc] initWithContentRect:NSMakeRect(0, 0, 1040, 790)
                                               styleMask:style
                                                 backing:NSBackingStoreBuffered
                                                   defer:NO];
    self.window.title = @"Agent Office";
    self.window.titleVisibility = NSWindowTitleHidden;
    self.window.titlebarAppearsTransparent = YES;
    self.window.collectionBehavior |= NSWindowCollectionBehaviorFullScreenPrimary;
    self.window.contentView = self.webView;
    self.window.delegate = self;
    self.window.releasedWhenClosed = NO;
    [self.window center];
}

- (void)openOfficeInPresentation:(BOOL)presentation {
    [self ensureServerShowingError:YES completion:^(BOOL success) {
        if (!success) return;
        [self makeWindowIfNeeded];
        NSString *path = presentation ? @"/presentation" : @"/";
        NSURL *url = [NSURL URLWithString:[@"http://127.0.0.1:4318" stringByAppendingString:path]];
        // Do not leave a previously loaded private office visible while the
        // presentation route is loading or entering full screen.
        self.webView.hidden = YES;
        self.revealWhenLoaded = YES;
        [self.webView loadRequest:[NSURLRequest requestWithURL:url]];
        self.window.title = presentation ? @"Agent Office Presentation" : @"Agent Office";
        [self.window makeKeyAndOrderFront:nil];
        [NSApp activateIgnoringOtherApps:YES];
        BOOL fullScreen = (self.window.styleMask & NSWindowStyleMaskFullScreen) != 0;
        if (presentation != fullScreen) [self.window toggleFullScreen:nil];
    }];
}

- (void)webView:(WKWebView *)webView didFinishNavigation:(WKNavigation *)navigation {
    (void)navigation;
    if (self.revealWhenLoaded) {
        self.revealWhenLoaded = NO;
        webView.hidden = NO;
    }
}

- (void)webView:(WKWebView *)webView
didFailProvisionalNavigation:(WKNavigation *)navigation
      withError:(NSError *)error {
    (void)webView;
    (void)navigation;
    self.revealWhenLoaded = NO;
    NSAlert *alert = [[NSAlert alloc] init];
    alert.messageText = @"Agent Office could not load";
    alert.informativeText = error.localizedDescription;
    alert.alertStyle = NSAlertStyleWarning;
    [alert runModal];
}

- (void)showOffice:(id)sender {
    (void)sender;
    [self openOfficeInPresentation:NO];
}

- (void)showPresentation:(id)sender {
    (void)sender;
    [self openOfficeInPresentation:YES];
}

- (void)refreshServerStatus {
    [self runController:@[@"status"] completion:^(BOOL success, NSString *output) {
        self.serverItem.title = success && [output containsString:@"\"running\": true"]
            ? @"Server: running locally" : @"Server: stopped";
    }];
}

- (void)refreshLoginStatus {
    [self runController:@[@"login-status", @"--app", [NSBundle mainBundle].bundleURL.path]
             completion:^(BOOL success, NSString *output) {
        BOOL enabled = success && [output containsString:@"\"enabled\": true"] &&
                       [output containsString:@"\"matches\": true"];
        self.loginItem.state = enabled ? NSControlStateValueOn : NSControlStateValueOff;
    }];
}

- (void)toggleLogin:(id)sender {
    (void)sender;
    NSArray<NSString *> *command = self.loginItem.state == NSControlStateValueOn
        ? @[@"disable-login"]
        : @[@"enable-login", @"--app", [NSBundle mainBundle].bundleURL.path];
    [self runController:command completion:^(BOOL success, NSString *output) {
        if (success) {
            [self refreshLoginStatus];
        } else {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Start at Login was not changed";
            alert.informativeText = output;
            alert.alertStyle = NSAlertStyleWarning;
            [alert runModal];
        }
    }];
}

- (void)openLog:(id)sender {
    (void)sender;
    NSURL *support = [[[NSFileManager defaultManager] homeDirectoryForCurrentUser]
        URLByAppendingPathComponent:@"Library/Application Support/Agent Office" isDirectory:YES];
    NSURL *log = [support URLByAppendingPathComponent:@"server.log"];
    if ([[NSFileManager defaultManager] fileExistsAtPath:log.path]) {
        [[NSWorkspace sharedWorkspace] activateFileViewerSelectingURLs:@[log]];
    } else {
        [[NSWorkspace sharedWorkspace] openURL:support];
    }
}

- (void)stopServer:(id)sender {
    (void)sender;
    [self runController:@[@"stop"] completion:^(BOOL success, NSString *output) {
        self.serverItem.title = success ? @"Server: stopped" : @"Server: stop failed";
        if (!success) {
            NSAlert *alert = [[NSAlert alloc] init];
            alert.messageText = @"Agent Office server was left running";
            alert.informativeText = output;
            alert.alertStyle = NSAlertStyleWarning;
            [alert runModal];
        }
    }];
}

- (void)quit:(id)sender {
    (void)sender;
    [NSApp terminate:nil];
}

- (void)windowWillClose:(NSNotification *)notification {
    (void)notification;
    self.window = nil;
    self.webView = nil;
    self.revealWhenLoaded = NO;
}

@end

int main(int argc, const char *argv[]) {
    (void)argc;
    (void)argv;
    @autoreleasepool {
        NSApplication *application = [NSApplication sharedApplication];
        AgentOfficeApp *delegate = [[AgentOfficeApp alloc] init];
        application.delegate = delegate;
        [application run];
    }
    return 0;
}
