#ifndef DETECTION_FRAMES_H
#define DETECTION_FRAMES_H

#include <stdint.h>
#include <stddef.h>

const uint32_t Person[] = {
    0xa0148120,
    0x09c1c801,
    0x0409402c0,
    0x00000002
};

const uint32_t Person2[] = {
    0x0804a0048,
    0x039004e05,
    0x002900d0,
    0x00000009
};

const uint32_t* const PersonFrames[] = { Person, Person2 };
const size_t PersonFramesCount = sizeof(PersonFrames) / sizeof(PersonFrames[0]);

#endif