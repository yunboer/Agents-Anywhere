from pathlib import Path
import base64
app=Path('ios/Agents Anywhere/Agents Anywhere')
support=Path('ios/Tests/ClientCoreTests/TestSupport.swift').read_text().replace('import Testing\n','').replace('@testable import ClientCore\n','')
a=support.index('@MainActor func eventually('); b=support.index('@MainActor final class TestGate',a)
support=support[:a]+support[b:]
encoded=base64.b64encode(Path('ios/Tests/ClientCoreTests/Fixtures/backend.json').read_bytes()).decode()
support+='''
func fixtureData(_ name: String) throws -> Data {
    let root = try JSONSerialization.jsonObject(with: Data(base64Encoded: "'''+encoded+'''")!) as! [String: Any]
    return try JSONSerialization.data(withJSONObject: root[name]!)
}
func fixture<Value: Decodable>(_ name: String, as: Value.Type = Value.self) throws -> Value {
    try JSONDecoder().decode(Value.self, from: fixtureData(name))
}
'''
# App target enables NonisolatedNonsendingByDefault; fixture transports must
# implement the nonisolated production protocols without inferred actor state.
support = support.replace('@MainActor final class TestHTTPTransport', 'nonisolated final class TestHTTPTransport').replace('@MainActor final class TestRealtimeAPI', 'nonisolated final class TestRealtimeAPI')
support = support.replace('\nfunc ', '\nnonisolated func ')
(app/'ValidationSupport.swift').write_text(support)
(app/'ValidationChatHost.swift').write_text(r'''
import SwiftUI

@MainActor private func validationChat() -> SessionChatModel {
    let http = TestHTTPTransport()
    http.respond = { call in
        if call.path.hasSuffix("snapshot") {
            var object = try fixtureObject("snapshot")
            object["notices"] = []
            var page = object["timeline"] as! [String: Any]
            page["items"] = try (561...600).map(validationItem)
            page["hasMore"] = true
            object["timeline"] = page
            return try JSONSerialization.data(withJSONObject: object)
        }
        if call.path.hasSuffix("timeline"), let before = call.query.first(where: { $0.name == "beforeOrderSeq" }).flatMap({ $0.value }).flatMap(Int.init) {
            try await Task.sleep(for: .milliseconds(500))
            var page = try fixtureObject("timeline")
            let start = max(1, before - 100)
            page["items"] = try (start..<before).map(validationItem)
            page["hasMore"] = start > 1
            return try JSONSerialization.data(withJSONObject: page)
        }
        return try http.defaultResponse(call)
    }
    let repo = repository(transport: http)
    return SessionChatModel(session: repo.session(id: "session"), repository: repo,
        attachments: .init(attachmentAPI: V2AttachmentAPI(transport: http)))
}
nonisolated private func validationItem(_ n: Int) throws -> [String: Any] {
    var item = try itemObject(id: "message-\(n)", order: n,
        text: "Validation message \(n)\n\n" + String(repeating: "Paragraph **bold** and `inline code` for scrolling. ", count: n % 5 + 1))
    item["type"] = "message"; item["role"] = n % 2 == 0 ? "assistant" : "user"
    item["status"] = "done"
    return item
}
struct ValidationChatHost: View {
    @State private var model = validationChat()
    @State private var streaming = false
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("Rows: \(model.timeline.rows.count)").accessibilityIdentifier("validation.count")
                Button("Stream") { streaming = true }.accessibilityIdentifier("validation.stream")
                Text(streaming ? "Streaming" : "Idle").accessibilityIdentifier("validation.status")
            }.padding(8)
            ChatTimelineView(model: model, onAttachment: { _ in }, onFile: { _ in })
        }
        .task {
            await model.prepareOpening()
            await model.timeline.run(sessionID: model.session.id, repository: model.repository)
        }
        .task(id: streaming) {
            guard streaming else { return }
            for revision in 2...20 {
                var item = try! validationItem(600)
                item["revision"] = revision
                item["status"] = revision == 20 ? "done" : "running"
                item["content"] = ["text": "Validation message 600\n\n" + String(repeating: "Streamed paragraph with **Markdown**.\n\n", count: revision)]
                let value: V2TimelineItem = try! decode(item)
                let items = model.timeline.rows.map { $0.id == value.id ? value : $0.value }
                model.timeline.stage(items, animate: true)
                model.timeline.flush(now: ProcessInfo.processInfo.systemUptime)
                try? await Task.sleep(for: .milliseconds(300))
            }
            streaming = false
        }
    }
}
''')
p=app/'App/AgentsAnywhereApp.swift'
s=p.read_text().replace('            RootView()', '            ValidationChatHost()')
p.write_text(s)
p=app/'Views/Chat/SessionTimelineEventView.swift'
s=p.read_text().replace('        .traceChatLayout("group:\\(group.id)",', '        .accessibilityElement(children: .contain)\n        .accessibilityIdentifier("validation.group.\\(group.id)")\n        .traceChatLayout("group:\\(group.id)",',1)
p.write_text(s)
Path('validation-ui').mkdir(exist_ok=True)
Path('validation-ui/project.yml').write_text('''name: TimelineUIValidation
options:
  deploymentTarget:
    iOS: "26.5"
targets:
  TimelineUITests:
    type: bundle.ui-testing
    platform: iOS
    sources: [TimelineUITests.swift]
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: com.agentsanywhere.validationtests
        GENERATE_INFOPLIST_FILE: YES
        CODE_SIGNING_ALLOWED: NO
schemes:
  TimelineUITests:
    build:
      targets:
        TimelineUITests: [test]
    test:
      targets: [TimelineUITests]
''')
Path('validation-ui/TimelineUITests.swift').write_text(r'''
import XCTest
final class TimelineUITests: XCTestCase {
    @MainActor func testHistoryReturnAndStreaming() throws {
        let app = XCUIApplication(bundleIdentifier: "com.agentsanywhere.app")
        app.launchArguments = ["-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        let count = app.staticTexts["validation.count"]
        XCTAssertTrue(count.waitForExistence(timeout: 20))
        XCTAssertTrue(wait { count.label == "Rows: 40" })
        let scroll = app.scrollViews.firstMatch
        XCTAssertTrue(scroll.waitForExistence(timeout: 10))
        let older = app.buttons["加载较早的消息"]
        for _ in 0..<60 {
            if older.isHittable { break }
            scroll.swipeDown(velocity: .fast)
        }
        XCTAssertTrue(older.isHittable, "Must reach the older-history prompt")
        let anchor = app.descendants(matching: .any).matching(identifier: "validation.group.message-561").firstMatch
        XCTAssertTrue(anchor.exists)
        let before = anchor.frame.minY
        attach(app, "Before prepend")
        older.tap()
        XCTAssertTrue(wait { count.label == "Rows: 140" })
        XCTAssertTrue(wait { !app.staticTexts["正在加载较早的消息…"].exists })
        XCTAssertTrue(wait { anchor.exists && abs(anchor.frame.minY - before) < 15 }, "Prepending history should preserve the measured anchor")
        attach(app, "After prepend")
        let bottom = app.buttons["chat.timeline.bottom"]
        XCTAssertTrue(wait { bottom.isHittable })
        bottom.tap()
        XCTAssertTrue(wait { !bottom.isHittable })
        attach(app, "Returned to bottom")
        app.buttons["validation.stream"].tap()
        XCTAssertTrue(wait { app.staticTexts["validation.status"].label == "Streaming" })
        XCTAssertTrue(wait { app.staticTexts["validation.status"].label == "Idle" })
        XCTAssertFalse(bottom.isHittable, "Streaming at the tail should keep following")
        attach(app, "After streaming")
        for _ in 0..<3 { scroll.swipeDown() }
        XCTAssertTrue(wait { bottom.isHittable })
        app.buttons["validation.stream"].tap()
        XCTAssertTrue(wait { app.staticTexts["validation.status"].label == "Streaming" })
        XCTAssertTrue(wait { app.staticTexts["validation.status"].label == "Idle" })
        XCTAssertTrue(bottom.isHittable, "Streaming must not pull a reader back to the tail")
        attach(app, "Reading during stream")
    }
    @MainActor private func wait(_ condition: @escaping () -> Bool) -> Bool {
        let expectation = XCTNSPredicateExpectation(predicate: NSPredicate { _, _ in condition() }, object: nil)
        return XCTWaiter.wait(for: [expectation], timeout: 20) == .completed
    }
    @MainActor private func attach(_ app: XCUIApplication, _ name: String) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name; attachment.lifetime = .keepAlways; add(attachment)
    }
}
''')
