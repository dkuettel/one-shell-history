{
  description = "one shell history";

  inputs = {
    nixpkgs.url = "github:dkuettel/nixpkgs/stable";
    flake-utils.url = "github:numtide/flake-utils";

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

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        name = "osh";

        pkgs = import nixpkgs {
          inherit system;
          config.allowUnfree = true;
        };
        inherit (pkgs) lib;
        inherit (builtins) map;

        python = pkgs.python313;

        uv = pkgs.writeScriptBin "uv" ''
          #!${pkgs.zsh}/bin/zsh
          set -eu -o pipefail
          UV_PYTHON=${python}/bin/python ${pkgs.uv}/bin/uv --no-python-downloads $@
        '';
        # for pyproject.toml
        #   [tool.uv.build-backend]
        #   namespace = true  # if you use namespace packages

        # NOTE prod needs it, but maybe not in the global namespace?
        # not sure how to, because of the shell scripts
        prodPkgs = [
          (pkgs.runCommandLocal "osh-prod-deps" { } ''
            mkdir -p $out/bin
            ln -sfT ${pkgs.fzf}/bin/fzf $out/bin/fzf
            ln -sfT ${pkgs.util-linux}/bin/uuidgen $out/bin/uuidgen
            ln -sfT ${pkgs.procps}/bin/pkill $out/bin/pkill
          '')
        ];

        devPkgs = (
          [
            uv
            python
          ]
          ++ (with pkgs; [
            ruff
            basedpyright
            nil # nix language server
            nixfmt-rfc-style # nixpkgs-fmt is deprecated
          ])
        );

        devLibs = with pkgs; [
          stdenv.cc.cc
          # zlib
          # libglvnd
          # xorg.libX11
          # glib
          # eigen
        ];

        devLdLibs = pkgs.buildEnv {
          name = "${name}-dev-ld-libs";
          paths = map (lib.getOutput "lib") devLibs;
        };

        devEnv = pkgs.buildEnv {
          name = "${name}-dev-env";
          paths = devPkgs ++ devLibs ++ prodPkgs;
        };

        pyproject = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };
        moduleOverrides =
          final: prev:
          let
            # see https://github.com/TyberiusPrime/uv2nix_hammer_overrides/tree/main
            # I dont fully understand what we do here, we switch to setuptools instead of wheels?
            # for libs that need to build for nix? and we might have to add build dependencies?
            setuptools =
              prev_lib:
              prev_lib.overrideAttrs (old: {
                nativeBuildInputs =
                  (old.nativeBuildInputs or [ ]) ++ (final.resolveBuildSystem { setuptools = [ ]; });
              });
          in
          {
            pprofile = setuptools prev.pprofile;
            pyprof2calltree = setuptools prev.pyprof2calltree;
          };
        modules =
          (pkgs.callPackage pyproject-nix.build.packages {
            python = python;
          }).overrideScope
            (
              lib.composeManyExtensions [
                pyproject-build-systems.overlays.default
                (pyproject.mkPyprojectOverlay { sourcePreference = "wheel"; })
                moduleOverrides
              ]
            );
        venv = modules.mkVirtualEnv "${name}-venv" pyproject.deps.default;
        inherit (pkgs.callPackages pyproject-nix.build.util { }) mkApplication;
        app = mkApplication {
          venv = venv;
          package = modules.osh;
        };
        package = pkgs.buildEnv {
          name = "${name}-env";
          paths = [ app ] ++ prodPkgs;
          postBuild = ''
            # TODO for example add some $out/share/zsh/site-functions/_name for completions
          '';
        };

        shell = pkgs.runCommandLocal "shell" { } ''
          mkdir -p $out
          ln -sfT ${./share} $out/share
        '';
      in
      {
        devShells.default = pkgs.mkShellNoCC {
          packages = [ devEnv ];
          LD_LIBRARY_PATH = "${pkgs.lib.makeLibraryPath [ devLdLibs ]}";
          shellHook = ''
            export PATH=$PWD/bin:$PATH
          '';
        };

        packages.default = package;
        packages.shell = shell;
      }
    );
}
