/*
 * Glibc 2.42 gave cfsetispeed and cfsetospeed new versions, which would
 * make freeglut's serial input, and so medit, need it. Bind them to the
 * versions every x86-64 glibc has, which take the same B9600.
 */
__asm__(".symver cfsetispeed,cfsetispeed@GLIBC_2.2.5");
__asm__(".symver cfsetospeed,cfsetospeed@GLIBC_2.2.5");
