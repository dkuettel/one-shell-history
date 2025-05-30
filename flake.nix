{
  description = "one shell history";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs?ref=nixos-24.11";

    # see https://pyproject-nix.github.io/uv2nix/usage/hello-world.html

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs = { self, nixpkgs, pyproject-nix, uv2nix, pyproject-build-systems, ... }@inputs:
    let
      inherit (nixpkgs) lib;
      system = "x86_64-linux";
      pkgs = nixpkgs.legacyPackages.${system};
      prod-dependencies = with pkgs; [ fzf ];
      nix-uv = pkgs.writeScriptBin "uv" ''
        #!${pkgs.zsh}/bin/zsh
        set -eu -o pipefail
        UV_PYTHON=${pkgs.python313}/bin/python ${pkgs.uv}/bin/uv --no-python-downloads $@
      '';
      dev = pkgs.buildEnv {
        name = "dev";
        # TODO util-linux  # for uuidgen
        paths = [ nix-uv ] ++ (with pkgs; [ python313 ruff basedpyright ]) ++ prod-dependencies;
        extraOutputsToInstall = [ "lib" ];
      };
      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
      overlay = workspace.mkPyprojectOverlay {
        sourcePreference = "wheel";
      };
      pyprojectOverrides = final: prev: {
        # from https://github.com/TyberiusPrime/uv2nix_hammer_overrides/tree/main
        # TODO but i dont understand why the right build system is not automatically used
        # TODO also how can they download pypi stuff without needing hashes to be updated?
        pprofile = prev.pprofile.overrideAttrs (old:
          { nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ (final.resolveBuildSystem { setuptools = [ ]; }); }
        );
        pyprof2calltree = prev.pprofile.overrideAttrs (old:
          { nativeBuildInputs = (old.nativeBuildInputs or [ ]) ++ (final.resolveBuildSystem { setuptools = [ ]; }); }
        );
      };
      pythonSet =
        (pkgs.callPackage pyproject-nix.build.packages {
          python = pkgs.python313;
        }).overrideScope
          (
            lib.composeManyExtensions [
              pyproject-build-systems.overlays.default
              overlay
              pyprojectOverrides
            ]
          );
      venv = pythonSet.mkVirtualEnv "osh-env" workspace.deps.default;
      env = pkgs.buildEnv {
        name = "osh env";
        paths = [ venv ] ++ prod-dependencies;
      };
      osh-prod = pkgs.writeScriptBin "osh" ''
        #!${pkgs.zsh}/bin/zsh
        set -eu -o pipefail
        path=(${env}/bin $path) python -m osh $@
      '';
      app = pkgs.runCommandLocal "osh" { } ''
        mkdir -p $out/bin
        ln -sfT ${osh-prod}/bin/osh $out/bin/osh
        ln -sfT ${./dev/osh-fzf} $out/bin/osh-fzf
      '';
      shell = pkgs.runCommandLocal "shell" { } ''
        mkdir -p $out
        ln -sfT ${./share} $out/share
      '';
    in
    {
      # > nix build .#name
      packages.${system} = {
        default = dev;
        venv = venv;
        dev = dev;
        app = app;
        shell = shell;
      };

      # > nix run .#name
      apps.${system}.default = { type = "app"; program = "${app}/bin/osh"; };
    };
}
