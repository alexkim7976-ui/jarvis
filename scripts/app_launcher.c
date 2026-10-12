// 범용 macOS 앱 런처 — 더블클릭 시 Contents/Resources/launch.sh 실행
// (launchd 가 셸 스크립트를 메인 executable 로 spawn 하지 못하는 macOS 대응)
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
        fprintf(stderr, "launcher: path error\n");
        return 1;
    }

    char real_path[PATH_MAX];
    if (realpath(exec_path, real_path) == NULL) {
        strncpy(real_path, exec_path, sizeof(real_path) - 1);
        real_path[sizeof(real_path) - 1] = '\0';
    }

    char buf[PATH_MAX];
    strncpy(buf, real_path, sizeof(buf) - 1);
    buf[sizeof(buf) - 1] = '\0';
    char macos[PATH_MAX];
    strncpy(macos, dirname(buf), sizeof(macos) - 1);
    macos[sizeof(macos) - 1] = '\0';
    char contents[PATH_MAX];
    strncpy(contents, dirname(macos), sizeof(contents) - 1);
    contents[sizeof(contents) - 1] = '\0';
    char bundle[PATH_MAX];
    strncpy(bundle, dirname(contents), sizeof(bundle) - 1);
    bundle[sizeof(bundle) - 1] = '\0';

    char launcher[PATH_MAX];
    snprintf(launcher, sizeof(launcher), "%s/Contents/Resources/launch.sh", bundle);
    setenv("LAUNCHER_APP_BUNDLE", bundle, 1);

    execl("/bin/bash", "AppLauncher", launcher, (char *)NULL);
    perror("launcher exec failed");
    return 1;
}
