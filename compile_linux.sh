#!/bin/bash -xe

mkdir -p distrib/linux64
cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
./configure --enable-static --disable-shared
make clean
make -j4
cd ..
cd dfu-util-0.8
USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util  ../distrib/linux64/
cd ..

mkdir -p distrib/linux32
cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
CFLAGS=-m32 ./configure
make clean
make -j4
cd ..
cd dfu-util-0.8
CFLAGS=-m32 USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/linux32
cd ..

mkdir -p distrib/arm
cd libusb-1.0.9
export LIBUSB_DIR=`pwd`
./configure --enable-static --disable-shared --host=arm-linux-gnueabihf
make clean
make -j4
cd ..
cd dfu-util-0.8
USB_CFLAGS="-I$LIBUSB_DIR/libusb/" USB_LIBS="-L$LIBUSB_DIR/libusb/.libs/ -lusb-1.0 -lpthread" ./configure --host=arm-linux-gnueabihf
make clean
CFLAGS=-static make
cp src/dfu-suffix src/dfu-prefix src/dfu-util ../distrib/arm/
