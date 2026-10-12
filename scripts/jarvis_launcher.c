// JARVIS.app 네이티브 진입점 (Mach-O 바이너리)
// 최신 macOS launchd 는 .app 의 메인 실행 파일로 셸 스크립트를 직접 spawn 하지
// 못한다(RBSRequestError 5 / errno 162 "Launchd job spawn failed"). 따라서 메인
// 실행 파일은 반드시 컴파일된 바이너리여야 한다. 이 바이너리는 자기 위치를 기준으로
// 번들 내 Contents/Resources/jarvis_launcher.sh 를 /bin/bash 로 실행한다.
#include <mach-o/dyld.h>
#include <libgen.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <limits.h>

int main(int argc, char *argv[]) {
    (void)argc; (void)argv;

    char exec_path[PATH_MAX];
    uint32_t size = sizeof(exec_path);
    if (_NSGetExecutablePath(exec_path, &size) != 0) {
        fprintf(stderr, "JARVIS: 실행 경로를 확인할 수 없습니다.\n");
        return 1;
    }

    char real_path[PATH_MAX];
    if (realpath(exec_path, real_path) == NULL) {
        strncpy(real_path, exec_path, sizeof(real_path) - 1);
        real_path[sizeof(real_path) - 1] = '\0';
    }

    // real_path = <BUNDLE>/Contents/MacOS/jarvis
    // dirname 을 3회 적용해 <BUNDLE> 을 구한다.
    char buf[PATH_MAX];
    strncpy(buf, real_path, sizeof(buf) - 1); buf[sizeof(buf) - 1] = '\0';
    char macos[PATH_MAX];
    strncpy(macos, dirname(buf), sizeof(macos) - 1); macos[sizeof(macos) - 1] = '\0';
    char contents[PATH_MAX];
    strncpy(contents, dirname(macos), sizeof(contents) - 1); contents[sizeof(contents) - 1] = '\0';
    char bundle[PATH_MAX];
    strncpy(bundle, dirname(contents), sizeof(bundle) - 1); bundle[sizeof(bundle) - 1] = '\0';

    char launcher[PATH_MAX];
    snprintf(launcher, sizeof(launcher), "%s/Contents/Resources/jarvis_launcher.sh", bundle);

    setenv("JARVIS_APP_BUNDLE", bundle, 1);
    setenv("JARVIS_APP_LAUNCH", "1", 1);
    setenv("CFBundleIdentifier", "com.jarvis.assistant", 1);
    setenv("__CFBundleIdentifier", "com.jarvis.assistant", 1);

    execl("/bin/bash", "J.A.R.V.I.S", launcher, (char *)NULL);

    perror("JARVIS: 런처 실행 실패");
    return 1;
}
