use std::path::PathBuf;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    println!("cargo:rustc-check-cfg=cfg(codex_bazel)");
    println!("cargo:rerun-if-changed=src/grpc");

    let mut config = tonic_prost_build::Config::new();
    // protoc-bin-vendored has no FreeBSD archive. FreeBSD builds use the
    // system protoc (or an explicit PROTOC path), while supported release
    // targets continue to use the pinned vendored binary.
    #[cfg(target_os = "freebsd")]
    let protoc = std::env::var_os("PROTOC")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("protoc"));
    #[cfg(not(target_os = "freebsd"))]
    let protoc = protoc_bin_vendored::protoc_bin_path()?;
    config.protoc_executable(protoc);
    let proto_files = glob::glob("src/grpc/*.proto")?.collect::<Result<Vec<_>, _>>()?;

    tonic_prost_build::configure()
        .build_client(/*enable*/ true)
        .build_server(/*enable*/ true)
        .compile_with_config(config, &proto_files, &[PathBuf::from("src/grpc")])?;

    Ok(())
}
