#include <stdio.h>

int main(int argc, char **argv, char **envp) {
  for (char **env = envp; *env != 0; ++env) {
    puts(*env);
  }

  for (int i=0; i < argc; ++i) {
    puts(argv[i]);
  }
  return 0;
}
