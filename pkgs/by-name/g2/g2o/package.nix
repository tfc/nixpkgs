{
  lib,
  stdenv,
  fetchFromGitHub,
  cmake,
  eigen,
  suitesparse,
  blas,
  lapack,
  libGLU,
  libsForQt5,
  spdlog,
}:

stdenv.mkDerivation rec {
  pname = "g2o";
  version = "20241228";

  src = fetchFromGitHub {
    owner = "RainerKuemmerle";
    repo = "g2o";
    rev = "${version}_git";
    hash = "sha256-MW1IO1P2e3KgurOW5ZfHlxK0m5sF0JhdLmvQNEHWEtI=";
  };

  # Removes a reference to gcc that is only used in a debug message
  patches = [ ./remove-compiler-reference.patch ];

  outputs = [
    "out"
    "dev"
  ];
  separateDebugInfo = true;

  nativeBuildInputs = [
    cmake
    libsForQt5.wrapQtAppsHook
  ];
  buildInputs = [
    eigen
    suitesparse
    blas
    lapack
    libGLU
    libsForQt5.qtbase
    libsForQt5.libqglviewer
  ];
  propagatedBuildInputs = [ spdlog ];

  dontWrapQtApps = true;

  cmakeFlags = [
    # Detection script is broken
    "-DQGLVIEWER_INCLUDE_DIR=${libsForQt5.libqglviewer}/include/QGLViewer"
    "-DG2O_BUILD_EXAMPLES=OFF"
  ]
  ++ lib.optionals stdenv.hostPlatform.isx86_64 [
    "-DDO_SSE_AUTODETECT=OFF"
    (lib.cmakeBool "DISABLE_SSE3" (!stdenv.hostPlatform.sse3Support))
    (lib.cmakeBool "DISABLE_SSE4_1" (!stdenv.hostPlatform.sse4_1Support))
    (lib.cmakeBool "DISABLE_SSE4_2" (!stdenv.hostPlatform.sse4_2Support))
    (lib.cmakeBool "DISABLE_SSE4_A" (!stdenv.hostPlatform.sse4_aSupport))
  ];

  meta = {
    description = "General Framework for Graph Optimization";
    homepage = "https://github.com/RainerKuemmerle/g2o";
    license = with lib.licenses; [
      bsd3
      lgpl3
      gpl3
    ];
    maintainers = with lib.maintainers; [ lopsided98 ];
    platforms = lib.platforms.all;
    # fatal error: 'qglviewer.h' file not found
    broken = stdenv.hostPlatform.isDarwin;
  };
}
