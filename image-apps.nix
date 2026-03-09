{
  pkgs' ? import <nixpkgs> { },
}:
let
  pkgs = import (pkgs'.applyPatches {
    src = ~/Repos/nixpkgs;
    patches = [ ./packaging2/mlflow.patch ];
  }) { };
in
pkgs.dockerTools.streamLayeredImage {
  name = "aibox-apps";
  tag = "latest";
  contents = pkgs.buildEnv {
    name = "image-root";
    extraPrefix = "/usr/local";
    paths = with pkgs; [
      (pkgs.python3.withPackages (
        p: with p; [
          supervisor
          huey
          (mlflow.overrideAttrs (old: {
            version = "3.10.0";
            src = pkgs.fetchFromGitHub {
              owner = "mlflow";
              repo = "mlflow";
              tag = "v3.10.0";
              hash = "sha256-9qprObKtzUvul3pwM7GR4eDQkO+szlTr06zVDBpWJS8=";
            };
            patches = old.patches or [ ] ++ [
              ./packaging2/launch-huey-with-shebang.patch
              ./patches/remove-sidebar-when-embed.patch
              ./patches/style-overrides-mlflow.patch
            ];
          }))
        ]
      ))
      (label-studio.overrideAttrs (old: {
        patches = [
          ../label-studio-extensions/patches/0006-Remove-sidebar.patch
          ../label-studio-extensions/patches/0008-Bypass-login.patch
          ./patches/style-overrides-labelstudio.patch
        ];
      }))
    ];
    pathsToLink = [
      "/bin"
      "/lib"
    ];
  };
}
