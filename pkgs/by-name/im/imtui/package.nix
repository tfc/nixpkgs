{
  lib,
  stdenv,
  fetchFromGitHub,
  cmake,
  imgui,
  ninja,
  withEmscripten ? false,
  emscripten,
  withCurl ? (!withEmscripten),
  curl,
  withNcurses ? (!withEmscripten),
  ncurses,
  static ? withEmscripten,
}:

stdenv.mkDerivation rec {
  pname = "imtui";
  version = "1.0.5";

  src = fetchFromGitHub {
    owner = "ggerganov";
    repo = "imtui";
    rev = "v${version}";
    hash = "sha256-eHQPDEfxKGLdiOi0lUUgqJcmme1XJLSPAafT223YK+U=";
  };

  nativeBuildInputs = [
    cmake
    ninja
  ];

  buildInputs =
    lib.optional withEmscripten emscripten
    ++ lib.optional withCurl curl
    ++ lib.optional withNcurses ncurses;

  postPatch = ''
    cp -r ${imgui.src}/* third-party/imgui/imgui
    chmod -R u+w third-party/imgui
  ''
  + lib.optionalString (lib.versionAtLeast imgui.version "1.90.1") ''
    substituteInPlace src/imtui-impl-{emscripten,ncurses}.cpp \
      --replace "ImGuiKey_KeyPadEnter" "ImGuiKey_KeypadEnter"
  '';

  cmakeFlags = [
    (lib.cmakeBool "EMSCRIPTEN" withEmscripten)
    (lib.cmakeBool "IMTUI_SUPPORT_CURL" withCurl)
    (lib.cmakeBool "IMTUI_SUPPORT_NCURSES" withNcurses)
    (lib.cmakeBool "BUILD_SHARED_LIBS" (!static))
    "-DIMTUI_BUILD_EXAMPLES:BOOL=OFF"
    "-DIMTUI_INSTALL_IMGUI_HEADERS:BOOL=OFF"
  ];

  meta = {
    description = "Immediate mode text-based user interface library";
    longDescription = ''
      ImTui is an immediate mode text-based user interface library. Supports 256
      ANSI colors and mouse/keyboard input.
    '';
    homepage = "https://imtui.ggerganov.com";
    changelog = "https://github.com/ggerganov/imtui/blob/${src.rev}/CHANGELOG.md";
    license = lib.licenses.mit;
    maintainers = [ ];
    platforms = lib.platforms.unix;
  };
}
