// swift-tools-version: 6.0
// The Mac app. ArityCore is the Swift side of the Rust core: build.sh writes its
// one source file (generated bindings) and the arity_coreFFI framework next to it.
import PackageDescription

let package = Package(
    name: "Arity",
    platforms: [.macOS(.v14)],
    targets: [
        .binaryTarget(name: "arity_coreFFI", path: "build/arity_coreFFI.xcframework"),
        .target(
            name: "ArityCore",
            dependencies: ["arity_coreFFI"],
            swiftSettings: [.swiftLanguageMode(.v5)],
            linkerSettings: [
                .linkedFramework("Security"),
                .linkedFramework("SystemConfiguration"),
                .linkedFramework("CoreFoundation"),
            ]
        ),
        .executableTarget(name: "Arity", dependencies: ["ArityCore"]),
    ]
)
