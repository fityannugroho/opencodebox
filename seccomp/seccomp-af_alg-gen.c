#include <seccomp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>

int main(int argc, char *argv[]) {
    scmp_filter_ctx ctx = NULL;
    uint32_t target_arch;
    uint32_t native_arch;
    int rc;

    // Parse optional architecture argument
    if (argc == 1) {
        target_arch = SCMP_ARCH_X86_64;
    } else if (argc == 2) {
        if (strcmp(argv[1], "x86_64") == 0) {
            target_arch = SCMP_ARCH_X86_64;
        } else if (strcmp(argv[1], "aarch64") == 0) {
            target_arch = SCMP_ARCH_AARCH64;
        } else {
            fprintf(stderr, "Invalid architecture: %s. Use x86_64 or aarch64.\n", argv[1]);
            return EXIT_FAILURE;
        }
    } else {
        fprintf(stderr, "Usage: %s [x86_64|aarch64]\n", argv[0]);
        return EXIT_FAILURE;
    }

    native_arch = seccomp_arch_native();

    // Initialize filter with default allow
    ctx = seccomp_init(SCMP_ACT_ALLOW);
    if (ctx == NULL) {
        fprintf(stderr, "seccomp_init failed\n");
        return EXIT_FAILURE;
    }

    // Add target architecture if different from native
    if (target_arch != native_arch) {
        rc = seccomp_arch_add(ctx, target_arch);
        if (rc < 0) {
            // EEXIST means arch already present, treat as success
            if (rc == -EEXIST) {
                rc = 0;
            } else {
                fprintf(stderr, "seccomp_arch_add failed: %s\n", strerror(-rc));
                seccomp_release(ctx);
                return EXIT_FAILURE;
            }
        }
        
        // Remove native architecture to ensure a single-architecture filter
        rc = seccomp_arch_remove(ctx, native_arch);
        if (rc < 0) {
            fprintf(stderr, "seccomp_arch_remove failed: %s\n", strerror(-rc));
            seccomp_release(ctx);
            return EXIT_FAILURE;
        }
    }

    // Block socket(AF_ALG) with EPERM
    rc = seccomp_rule_add(
        ctx,
        SCMP_ACT_ERRNO(EPERM),
        SCMP_SYS(socket),
        1,
        SCMP_A0(SCMP_CMP_EQ, 38)
    );
    if (rc < 0) {
        fprintf(stderr, "seccomp_rule_add failed: %s\n", strerror(-rc));
        seccomp_release(ctx);
        return EXIT_FAILURE;
    }

    // Export BPF to stdout
    rc = seccomp_export_bpf(ctx, STDOUT_FILENO);
    if (rc < 0) {
        fprintf(stderr, "seccomp_export_bpf failed: %s\n", strerror(-rc));
        seccomp_release(ctx);
        return EXIT_FAILURE;
    }

    seccomp_release(ctx);
    return EXIT_SUCCESS;
}
