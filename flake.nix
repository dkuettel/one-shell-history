{
  description = "decals";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-unstable";
  };

  outputs = { self, nixpkgs }:
    let
      pkgs = import nixpkgs { system = "x86_64-linux"; };
    in
    rec {
      # devShells.x86_64-linux.default = pkgs.mkShell {
      #   packages = [
      #     pkgs.python313
      #     pkgs.uv
      #     pkgs.ruff
      #   ];
      #   shellHook = "exec zsh";
      # };
      # packages.x86_64-linux.default = devShells.x86_64-linux.default;
      env = pkgs.buildEnv {
        name = "dev";
        paths = [
          pkgs.python312
          pkgs.uv
          pkgs.ruff
          pkgs.pyright
          pkgs.stdenv.cc.cc.lib # for msgpack
          pkgs.nodejs_22 # for copilot
          pkgs.fzf
        ];
      };
      packages.x86_64-linux.default = env;
    };
}
