#!/bin/bash -xe

mkdir -p distrib/osx
cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
CC=o64-clang ./configure --host=x86_64-apple-darwin13 --enable-static --disable-shared
make clean
make -j4
cd ..
cd dfu-util-0.8
CC=o64-clang USB_CFLAGS="-I$LIBUSB_DIR/libusb/ -framework IOKit -framework CoreFoundation" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0" ./configure --host=x86_64-apple-darwin13
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/osx/
#cd libusb-1.0.9 && make distclean && cd..
#cd dfu-util-0.8 && make distclean && cd..
